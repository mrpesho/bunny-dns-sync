"""
Main orchestrator for syncing bunny.net configuration.
"""

import json
from pathlib import Path
from typing import Any, Optional, Union

from .bunny_client import BunnyClient
from .dns_manager import DNSManager
from .pullzone_manager import PullZoneManager
from .edge_rules_manager import EdgeRulesManager


class BunnySync:
    """Orchestrates syncing DNS zones, Pull Zones, and Edge Rules."""

    def __init__(self, api_key: str):
        self.client = BunnyClient(api_key)
        self.dns_manager = DNSManager(self.client)
        self.pullzone_manager = PullZoneManager(self.client)
        self.edge_rules_manager = EdgeRulesManager(self.client)

    def load_config(self, config: Union[dict, str, Path]) -> dict:
        """
        Load configuration from dict, JSON string, or file path.

        Args:
            config: Configuration as dict, JSON string, or path to JSON file

        Returns:
            Parsed configuration dict
        """
        if isinstance(config, dict):
            return config
        elif isinstance(config, Path) or (isinstance(config, str) and Path(config).exists()):
            path = Path(config)
            with open(path) as f:
                return json.load(f)
        elif isinstance(config, str):
            return json.loads(config)
        else:
            raise ValueError(f"Invalid config type: {type(config)}")

    def _filter_domains(self, domains_config: dict, domain_filter: Optional[str]) -> dict:
        """Filter domains config by domain name if filter is specified."""
        if domain_filter is None:
            return domains_config
        # Match exact domain or allow wildcard matching
        filtered = {}
        for domain, config in domains_config.items():
            if domain.lower() == domain_filter.lower():
                filtered[domain] = config
        return filtered

    def sync(
        self,
        config: Union[dict, str, Path],
        dry_run: bool = False,
        delete_extra_records: bool = True,
        domain: Optional[str] = None,
    ) -> dict:
        """
        Sync all resources to match configuration.

        Args:
            config: Configuration dict, JSON string, or path to JSON file
            dry_run: If True, only report changes without making them
            delete_extra_records: If True, delete DNS records not in config
            domain: If specified, only sync this domain (and its pull zones)

        Returns:
            Dict with all sync results
        """
        config_data = self.load_config(config)
        results = {
            "dry_run": dry_run,
            "domain_filter": domain,
            "dns_zones": [],
            "pull_zones": [],
            "summary": {
                "dns_records_created": 0,
                "dns_records_updated": 0,
                "dns_records_deleted": 0,
                "pull_zones_created": 0,
                "pull_zones_updated": 0,
                "pull_zones_deleted": 0,
                "hostnames_added": 0,
                "hostnames_removed": 0,
                "edge_rules_created": 0,
                "edge_rules_deleted": 0,
            },
        }

        # Get domains config and apply filter
        domains_config = config_data.get("domains", {})
        domains_config = self._filter_domains(domains_config, domain)

        if domain and not domains_config:
            raise ValueError(f"Domain '{domain}' not found in configuration")

        # Process each domain
        for domain_name, domain_config in domains_config.items():
            # Sync DNS records for this domain
            dns_records = domain_config.get("dns_records", [])
            result = self.dns_manager.sync_zone(
                domain=domain_name,
                desired_records=dns_records,
                dry_run=dry_run,
                delete_extra=delete_extra_records,
            )
            results["dns_zones"].append(result)
            results["summary"]["dns_records_created"] += len(result.get("created", []))
            results["summary"]["dns_records_updated"] += len(result.get("updated", []))
            results["summary"]["dns_records_deleted"] += len(result.get("deleted", []))

            # Sync Pull Zones for this domain
            pull_zones_config = domain_config.get("pull_zones", {})
            for pz_name, pz_config in pull_zones_config.items():
                # Sync the pull zone itself
                pz_result = self.pullzone_manager.sync_zone(
                    name=pz_name,
                    config=pz_config,
                    dry_run=dry_run,
                )
                pz_result["domain"] = domain_name
                results["pull_zones"].append(pz_result)

                if pz_result.get("created"):
                    results["summary"]["pull_zones_created"] += 1
                if pz_result.get("updated"):
                    results["summary"]["pull_zones_updated"] += 1
                results["summary"]["hostnames_added"] += len(pz_result.get("hostnames_added", []))
                results["summary"]["hostnames_removed"] += len(pz_result.get("hostnames_removed", []))

                # Sync edge rules for this pull zone
                edge_rules_config = pz_config.get("edge_rules", [])
                if edge_rules_config:
                    # Get the pull zone ID
                    zone = self.pullzone_manager.get_zone_by_name(pz_name)
                    if zone:
                        er_result = self.edge_rules_manager.sync_rules(
                            zone_id=zone.id,
                            rule_configs=edge_rules_config,
                            dry_run=dry_run,
                        )
                        pz_result["edge_rules"] = er_result
                        results["summary"]["edge_rules_created"] += len(er_result.get("created", []))
                        results["summary"]["edge_rules_deleted"] += len(er_result.get("deleted", []))

            # Delete extra pull zones not in config
            if delete_extra_records:
                configured_pz_names = {
                    name.lower() for name in pull_zones_config.keys()
                }
                remote_zones = self.pullzone_manager.get_zones_for_domain(domain_name)
                for remote_zone in remote_zones:
                    if remote_zone.name.lower() not in configured_pz_names:
                        results["pull_zones"].append({
                            "zone": remote_zone.name,
                            "domain": domain_name,
                            "deleted": True,
                            "changes": [f"Deleting pull zone '{remote_zone.name}'"],
                        })
                        results["summary"]["pull_zones_deleted"] += 1
                        if not dry_run:
                            self.pullzone_manager.delete_zone(remote_zone.id)

        return results

    def sync_dns_only(
        self,
        config: Union[dict, str, Path],
        dry_run: bool = False,
        delete_extra_records: bool = True,
        domain: Optional[str] = None,
    ) -> dict:
        """Sync only DNS zones."""
        config_data = self.load_config(config)
        results = {"dry_run": dry_run, "domain_filter": domain, "dns_zones": []}

        domains_config = config_data.get("domains", {})
        domains_config = self._filter_domains(domains_config, domain)

        if domain and not domains_config:
            raise ValueError(f"Domain '{domain}' not found in configuration")

        for domain_name, domain_config in domains_config.items():
            dns_records = domain_config.get("dns_records", [])
            result = self.dns_manager.sync_zone(
                domain=domain_name,
                desired_records=dns_records,
                dry_run=dry_run,
                delete_extra=delete_extra_records,
            )
            results["dns_zones"].append(result)

        return results

    def pull(
        self,
        domain: Optional[str] = None,
        pull_all: bool = False,
        dns_only: bool = False,
        pullzones_only: bool = False,
    ) -> dict:
        """Pull current state from bunny.net and return as config dict.

        Args:
            domain: Pull a specific domain
            pull_all: Pull all DNS zones on the account
            dns_only: Only pull DNS records
            pullzones_only: Only pull Pull Zones

        Returns:
            Config dict matching the standard config format
        """
        if pull_all:
            return self._pull_all_domains(
                dns_only=dns_only, pullzones_only=pullzones_only
            )
        elif domain:
            return self._pull_domain(
                domain, dns_only=dns_only, pullzones_only=pullzones_only
            )
        else:
            raise ValueError("--sot bunny requires either --domain or --all")

    def _pull_domain(
        self,
        domain: str,
        dns_only: bool = False,
        pullzones_only: bool = False,
    ) -> Optional[dict]:
        """Pull config for a single domain.

        Returns:
            Config dict, or None if the domain was not found on the account.
        """
        domain_config = {}
        zone_found = True

        if not pullzones_only:
            records = self.dns_manager.export_zone(domain)
            if records is None:
                zone_found = False
                records = []
            domain_config["dns_records"] = records

        if not dns_only:
            pull_zones = {}
            pz_list = self.pullzone_manager.get_zones_for_domain(domain)
            for pz in pz_list:
                pz_config = pz.to_config_dict()
                pz_config["edge_rules"] = self.edge_rules_manager.export_rules(pz.id)
                pull_zones[pz.name] = pz_config
            domain_config["pull_zones"] = pull_zones

        # If DNS zone wasn't found and no pull zones matched, domain doesn't exist
        if not zone_found and not domain_config.get("pull_zones"):
            return None

        return {"domains": {domain: domain_config}}

    def _pull_all_domains(
        self,
        dns_only: bool = False,
        pullzones_only: bool = False,
    ) -> dict:
        """Pull config for all domains on the account."""
        domains = {}

        # Get all DNS zones
        if not pullzones_only:
            all_dns = self.dns_manager.export_all_zones()
            for domain_name, records in all_dns.items():
                domains.setdefault(domain_name, {})["dns_records"] = records

        # Get all Pull Zones and associate to domains
        if not dns_only:
            all_pz = self.pullzone_manager.list_zones()
            dns_domains = list(domains.keys()) if domains else [
                z.domain for z in self.dns_manager.list_zones()
            ]

            for pz in all_pz:
                pz_config = pz.to_config_dict()
                pz_config["edge_rules"] = self.edge_rules_manager.export_rules(pz.id)

                # Find matching domain by hostname
                matched_domain = None
                for h in pz.hostnames:
                    if h.is_system_hostname:
                        continue
                    h_lower = h.value.lower()
                    for d in dns_domains:
                        d_lower = d.lower()
                        if h_lower == d_lower or h_lower.endswith("." + d_lower):
                            matched_domain = d
                            break
                    if matched_domain:
                        break

                if matched_domain:
                    domains.setdefault(matched_domain, {})
                    domains[matched_domain].setdefault("pull_zones", {})[pz.name] = pz_config
                else:
                    import sys
                    print(
                        f"Warning: Pull zone '{pz.name}' could not be matched to any domain",
                        file=sys.stderr,
                    )

            # Ensure all domains have pull_zones key
            for d in domains:
                domains[d].setdefault("pull_zones", {})

        return {"domains": domains}

    def sync_pullzones_only(
        self,
        config: Union[dict, str, Path],
        dry_run: bool = False,
        domain: Optional[str] = None,
    ) -> dict:
        """Sync only Pull Zones (without edge rules)."""
        config_data = self.load_config(config)
        results = {"dry_run": dry_run, "domain_filter": domain, "pull_zones": []}

        domains_config = config_data.get("domains", {})
        domains_config = self._filter_domains(domains_config, domain)

        if domain and not domains_config:
            raise ValueError(f"Domain '{domain}' not found in configuration")

        for domain_name, domain_config in domains_config.items():
            pull_zones_config = domain_config.get("pull_zones", {})
            for pz_name, pz_config in pull_zones_config.items():
                result = self.pullzone_manager.sync_zone(
                    name=pz_name,
                    config=pz_config,
                    dry_run=dry_run,
                )
                result["domain"] = domain_name
                results["pull_zones"].append(result)

        return results


def print_results(results: dict) -> None:
    """Print sync results in a human-readable format."""
    if results.get("dry_run"):
        print("=== DRY RUN MODE (no changes made) ===\n")
    if results.get("domain_filter"):
        print(f"=== Syncing domain: {results['domain_filter']} ===\n")

    # DNS Zones
    if results.get("dns_zones"):
        print("DNS ZONES:")
        print("-" * 40)
        for zone in results["dns_zones"]:
            print(f"\n  {zone['zone']}:")
            if zone.get("zone_created"):
                print("    [NEW ZONE CREATED]")
            if zone.get("created"):
                print(f"    Created: {len(zone['created'])} records")
                for rec in zone["created"]:
                    print(f"      + {rec}")
            if zone.get("updated"):
                print(f"    Updated: {len(zone['updated'])} records")
                for rec in zone["updated"]:
                    print(f"      ~ {rec}")
            if zone.get("deleted"):
                print(f"    Deleted: {len(zone['deleted'])} records")
                for rec in zone["deleted"]:
                    print(f"      - {rec}")
            if zone.get("unchanged"):
                print(f"    Unchanged: {len(zone['unchanged'])} records")

    # Pull Zones
    if results.get("pull_zones"):
        print("\nPULL ZONES:")
        print("-" * 40)
        for zone in results["pull_zones"]:
            print(f"\n  {zone['zone']}:")
            if zone.get("created"):
                print("    [NEW ZONE CREATED]")
            if zone.get("deleted"):
                print("    [ZONE DELETED]")
            if zone.get("updated"):
                print("    [ZONE UPDATED]")
            for change in zone.get("changes", []):
                print(f"    {change}")
            if zone.get("edge_rules"):
                er = zone["edge_rules"]
                if er.get("deleted"):
                    print(f"    Edge rules deleted: {len(er['deleted'])}")
                if er.get("created"):
                    print(f"    Edge rules created: {len(er['created'])}")
                    for rule in er["created"]:
                        print(f"      + {rule}")

    # Summary
    if results.get("summary"):
        s = results["summary"]
        print("\nSUMMARY:")
        print("-" * 40)
        print(f"  DNS records: {s['dns_records_created']} created, "
              f"{s['dns_records_updated']} updated, "
              f"{s['dns_records_deleted']} deleted")
        print(f"  Pull zones: {s['pull_zones_created']} created, "
              f"{s['pull_zones_updated']} updated, "
              f"{s['pull_zones_deleted']} deleted")
        print(f"  Hostnames: {s['hostnames_added']} added, "
              f"{s['hostnames_removed']} removed")
        print(f"  Edge rules: {s['edge_rules_created']} created, "
              f"{s['edge_rules_deleted']} deleted")
