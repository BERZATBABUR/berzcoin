#!/usr/bin/env python3
"""DNS seed helper for BerzCoin network.

This is an operator tool that generates a BIND-compatible zone file from a list
of known node IPs. Running an actual DNS server is out of scope here.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import List


class DNSSeedServer:
    """DNS seed zone generator for BerzCoin."""

    def __init__(self, domain: str, nodes_file: str = "known_nodes.json"):
        self.domain = domain.strip(".")
        self.nodes_file = nodes_file
        self.nodes: List[str] = []
        self.update_interval = 3600  # 1 hour

    async def load_nodes(self) -> None:
        """Load known nodes from file."""
        try:
            with open(self.nodes_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            nodes = data.get("nodes", [])
            if isinstance(nodes, list):
                self.nodes = [str(n).strip() for n in nodes if str(n).strip()]
            else:
                self.nodes = []
            print(f"Loaded {len(self.nodes)} nodes")
        except Exception as e:
            print(f"Failed to load nodes: {e}")
            self.nodes = []

    async def run(self) -> None:
        """Generate a zone file for BIND/named."""
        await self._generate_zone_file()

    async def _generate_zone_file(self) -> None:
        await self.load_nodes()

        serial = int(time.time())
        zone = f\"\"\"$ORIGIN {self.domain}.
$TTL 3600
@       IN SOA ns1.{self.domain}. admin.{self.domain}. (
            {serial}  ; Serial
            3600      ; Refresh
            900       ; Retry
            86400     ; Expire
            3600      ; Minimum
        )
        IN NS ns1.{self.domain}.
        IN NS ns2.{self.domain}.

ns1     IN A YOUR_NAMESERVER_IP
ns2     IN A YOUR_NAMESERVER_IP
\"\"\"

        for node in self.nodes:
            zone += f\"seed\\tIN A\\t{node}\\n\"

        out = Path(f\"{self.domain}.zone\")
        out.write_text(zone, encoding="utf-8")
        print(f\"DNS zone file generated: {out}\")


async def _amain() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Generate DNS seed zone file")
    parser.add_argument("--domain", required=True, help="seed domain (e.g. seed.berzcoin.org)")
    parser.add_argument("--nodes-file", default="known_nodes.json", help="JSON file with {'nodes': ['IP', ...]}")
    args = parser.parse_args()

    srv = DNSSeedServer(domain=args.domain, nodes_file=args.nodes_file)
    await srv.run()


def main() -> None:
    import asyncio

    asyncio.run(_amain())


if __name__ == "__main__":
    main()

