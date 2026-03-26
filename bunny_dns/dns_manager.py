"""
DNS Zone and Record management for bunny.net.
"""

import ipaddress
from dataclasses import dataclass
from typing import Optional

from .bunny_client import BunnyClient, BunnyNotFoundError


# DNS Record type mapping
DNS_RECORD_TYPES = {
    "A": 0,
    "AAAA": 1,
    "CNAME": 2,
    "TXT": 3,
    "MX": 4,
    "RDR": 5,      # Bunny.NET Redirect
    "PZ": 7,       # Bunny.NET Pull Zone
    "SRV": 8,
    "CAA": 9,
    "PTR": 10,
    "SCR": 11,     # Bunny.NET Script
    "NS": 12,
}

DNS_RECORD_TYPES_REVERSE = {v: k for k, v in DNS_RECORD_TYPES.items()}


@dataclass
class DNSRecord:
    """Represents a DNS record."""
    type: str
    name: str
    value: str
    ttl: int = 300
    priority: Optional[int] = None  # For MX, SRV
    weight: Optional[int] = None    # For SRV
    port: Optional[int] = None      # For SRV
    id: Optional[int] = None        # Set when fetched from API

    def to_config_dict(self) -> dict:
        """Convert to config format (inverse of from_api_response)."""
        name = "@" if self.name == "" else self.name
        d = {
            "type": self.type.upper(),
            "name": name,
            "value": self.value,
            "ttl": self.ttl,
        }
        if self.priority is not None and self.priority != 0:
            d["priority"] = self.priority
        if self.weight is not None and self.weight != 0:
            d["weight"] = self.weight
        if self.port is not None and self.port != 0:
            d["port"] = self.port
        return d

    def to_api_payload(self) -> dict:
        """Convert to API request payload."""
        payload = {
            "Type": DNS_RECORD_TYPES[self.type.upper()],
            "Name": self.name,
            "Value": self.value,
            "Ttl": self.ttl,
        }
        if self.priority is not None:
            payload["Priority"] = self.priority
        if self.weight is not None:
            payload["Weight"] = self.weight
        if self.port is not None:
            payload["Port"] = self.port
        return payload

    @classmethod
    def from_api_response(cls, data: dict) -> "DNSRecord":
        """Create DNSRecord from API response."""
        record_type = DNS_RECORD_TYPES_REVERSE.get(data.get("Type", 0), "A")
        return cls(
            id=data.get("Id"),
            type=record_type,
            name=data.get("Name", ""),
            value=data.get("Value", ""),
            ttl=data.get("Ttl", 300),
            priority=data.get("Priority"),
            weight=data.get("Weight"),
            port=data.get("Port"),
        )

    def _normalize_name(self, name: str) -> str:
        """Normalize record name - treat @ and empty string as equivalent (root domain)."""
        n = name.lower().strip()
        return "" if n == "@" else n

    def _normalize_value(self, value: str, record_type: str) -> str:
        """Normalize record value - expand IPv6 addresses to allow comparison."""
        if record_type.upper() == "AAAA":
            try:
                return str(ipaddress.IPv6Address(value))
            except ValueError:
                pass
        return value

    def matches(self, other: "DNSRecord") -> bool:
        """Check if two records match (same type, name, value)."""
        return (
            self.type.upper() == other.type.upper()
            and self._normalize_name(self.name) == self._normalize_name(other.name)
            and self._normalize_value(self.value, self.type) == self._normalize_value(other.value, other.type)
        )

    def _normalize_optional(self, val) -> int:
        """Normalize optional int fields - treat None and 0 as equivalent."""
        return 0 if val is None else val

    def needs_update(self, other: "DNSRecord") -> bool:
        """Check if this record needs to be updated to match other."""
        if not self.matches(other):
            return False
        return (
            self.ttl != other.ttl
            or self._normalize_optional(self.priority) != self._normalize_optional(other.priority)
            or self._normalize_optional(self.weight) != self._normalize_optional(other.weight)
            or self._normalize_optional(self.port) != self._normalize_optional(other.port)
        )


@dataclass
class DNSZone:
    """Represents a DNS zone."""
    domain: str
    id: Optional[int] = None
    records: list[DNSRecord] = None

    def __post_init__(self):
        if self.records is None:
            self.records = []

    @classmethod
    def from_api_response(cls, data: dict) -> "DNSZone":
        """Create DNSZone from API response."""
        records = [
            DNSRecord.from_api_response(r)
            for r in data.get("Records", [])
        ]
        return cls(
            id=data.get("Id"),
            domain=data.get("Domain", ""),
            records=records,
        )


class DNSManager:
    """Manages DNS zones and records on bunny.net."""

    def __init__(self, client: BunnyClient):
        self.client = client

    def list_zones(self) -> list[DNSZone]:
        """List all DNS zones."""
        response = self.client.get("/dnszone")
        items = response.get("Items", []) if response else []
        return [DNSZone.from_api_response(z) for z in items]

    def get_zone(self, zone_id: int) -> DNSZone:
        """Get a DNS zone by ID."""
        response = self.client.get(f"/dnszone/{zone_id}")
        return DNSZone.from_api_response(response)

    def get_zone_by_domain(self, domain: str) -> Optional[DNSZone]:
        """Find a DNS zone by domain name."""
        zones = self.list_zones()
        for zone in zones:
            if zone.domain.lower() == domain.lower():
                # Fetch full zone with records
                return self.get_zone(zone.id)
        return None

    def create_zone(self, domain: str) -> DNSZone:
        """Create a new DNS zone."""
        response = self.client.post("/dnszone", {"Domain": domain})
        return DNSZone.from_api_response(response)

    def delete_zone(self, zone_id: int) -> None:
        """Delete a DNS zone."""
        self.client.delete(f"/dnszone/{zone_id}")

    def add_record(self, zone_id: int, record: DNSRecord) -> DNSRecord:
        """Add a DNS record to a zone."""
        payload = record.to_api_payload()
        response = self.client.put(f"/dnszone/{zone_id}/records", payload)
        return DNSRecord.from_api_response(response)

    def update_record(self, zone_id: int, record_id: int, record: DNSRecord) -> None:
        """Update an existing DNS record."""
        payload = record.to_api_payload()
        payload["Id"] = record_id
        self.client.post(f"/dnszone/{zone_id}/records/{record_id}", payload)

    def delete_record(self, zone_id: int, record_id: int) -> None:
        """Delete a DNS record."""
        self.client.delete(f"/dnszone/{zone_id}/records/{record_id}")

    def export_zone(self, domain: str) -> Optional[list[dict]]:
        """Export DNS records for a zone as config dicts.

        Returns:
            List of record config dicts, or None if zone not found.
        """
        zone = self.get_zone_by_domain(domain)
        if zone is None:
            return None
        return [r.to_config_dict() for r in zone.records]

    def export_all_zones(self) -> dict[str, list[dict]]:
        """Export all DNS zones as {domain: [records]}.

        Returns:
            Dict mapping domain names to lists of record config dicts.
        """
        result = {}
        zones = self.list_zones()
        for zone_summary in zones:
            zone = self.get_zone(zone_summary.id)
            result[zone.domain] = [r.to_config_dict() for r in zone.records]
        return result

    def sync_zone(
        self,
        domain: str,
        desired_records: list[dict],
        dry_run: bool = False,
        delete_extra: bool = True,
    ) -> dict:
        """
        Sync DNS records for a zone to match desired state.

        Args:
            domain: Domain name of the zone
            desired_records: List of record dicts from config
            dry_run: If True, only report changes without making them
            delete_extra: If True, delete records not in config

        Returns:
            Dict with counts of created, updated, deleted records
        """
        result = {
            "zone": domain,
            "created": [],
            "updated": [],
            "deleted": [],
            "unchanged": [],
        }

        # Get or create zone
        zone = self.get_zone_by_domain(domain)
        if zone is None:
            if dry_run:
                result["zone_created"] = True
                # In dry run, we can't sync records for a non-existent zone
                for rec in desired_records:
                    result["created"].append(f"{rec['type']} {rec['name']} -> {rec['value']}")
                return result
            zone = self.create_zone(domain)
            result["zone_created"] = True

        # Convert desired records to DNSRecord objects
        desired = [
            DNSRecord(
                type=r["type"],
                name=r["name"],
                value=r["value"],
                ttl=r.get("ttl", 300),
                priority=r.get("priority"),
                weight=r.get("weight"),
                port=r.get("port"),
            )
            for r in desired_records
        ]

        # Current records from API
        current = zone.records

        # Track which current records are matched
        matched_current_ids = set()

        # Classify records into create, update, unchanged
        to_create = []
        for desired_rec in desired:
            found = False
            for current_rec in current:
                if desired_rec.matches(current_rec):
                    found = True
                    matched_current_ids.add(current_rec.id)
                    if current_rec.needs_update(desired_rec):
                        desc = f"{desired_rec.type} {desired_rec.name} -> {desired_rec.value}"
                        result["updated"].append(desc)
                        if not dry_run:
                            desired_rec.id = current_rec.id
                            self.update_record(zone.id, current_rec.id, desired_rec)
                    else:
                        result["unchanged"].append(
                            f"{desired_rec.type} {desired_rec.name} -> {desired_rec.value}"
                        )
                    break
            if not found:
                to_create.append(desired_rec)

        # Delete extra records before creating new ones to avoid conflicts
        # (e.g. changing www from CNAME to A requires deleting the CNAME first)
        if delete_extra:
            for current_rec in current:
                if current_rec.id not in matched_current_ids:
                    desc = f"{current_rec.type} {current_rec.name} -> {current_rec.value}"
                    result["deleted"].append(desc)
                    if not dry_run:
                        self.delete_record(zone.id, current_rec.id)

        # Create new records (after deletes to avoid CNAME conflicts)
        for desired_rec in to_create:
            desc = f"{desired_rec.type} {desired_rec.name} -> {desired_rec.value}"
            result["created"].append(desc)
            if not dry_run:
                self.add_record(zone.id, desired_rec)

        return result
