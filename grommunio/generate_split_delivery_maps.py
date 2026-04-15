#!/usr/bin/env python3
"""
Render synchronized Mailu and grommunio Postfix maps for split delivery.

The input inventory is a plain text file with one grommunio-owned mailbox
address per line. Blank lines and comments are ignored.
"""

from __future__ import annotations

import argparse
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--inventory",
        default="split-delivery/grommunio-mailboxes.txt",
        help="Path to the split-delivery inventory file.",
    )
    parser.add_argument(
        "--mailu-transport-out",
        required=True,
        help="Path to write the Mailu recipient transport map.",
    )
    parser.add_argument(
        "--mailu-valid-out",
        required=True,
        help="Path to write the Mailu virtual mailbox validation map.",
    )
    parser.add_argument(
        "--grommunio-transport-out",
        required=True,
        help="Path to write the grommunio transport seed file.",
    )
    parser.add_argument(
        "--mailu-grommunio-nexthop",
        default="smtp:[grommunio-internal]:24",
        help="Mailu next hop for grommunio-owned recipients.",
    )
    parser.add_argument(
        "--grommunio-local-nexthop",
        default="smtp:[127.0.0.1]:24",
        help="grommunio next hop for grommunio-owned recipients.",
    )
    parser.add_argument(
        "--grommunio-mailu-nexthop",
        default="smtp:[mailu-smtp]:25",
        help="grommunio next hop for same-domain recipients owned by Mailu.",
    )
    return parser.parse_args()


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def load_inventory(path: Path) -> list[str]:
    addresses: list[str] = []
    seen: set[str] = set()
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].strip().lower()
        if not line:
            continue
        if line.count("@") != 1:
            raise SystemExit(f"invalid mailbox entry in {path}: {raw_line!r}")
        if line in seen:
            continue
        seen.add(line)
        addresses.append(line)
    if not addresses:
        raise SystemExit(f"split-delivery inventory is empty: {path}")
    return sorted(addresses)


def write_map(path: Path, lines: list[str]) -> None:
    ensure_parent(path)
    content = "\n".join(lines) + "\n"
    path.write_text(content, encoding="utf-8")


def main() -> int:
    args = parse_args()

    inventory_path = Path(args.inventory)
    addresses = load_inventory(inventory_path)
    domains = sorted({address.split("@", 1)[1] for address in addresses})

    mailu_transport_lines = [
        f"{address} {args.mailu_grommunio_nexthop}"
        for address in addresses
    ]
    mailu_valid_lines = [f"{address} 1" for address in addresses]

    grommunio_transport_lines = [
        f"{address} {args.grommunio_local_nexthop}"
        for address in addresses
    ]
    grommunio_transport_lines.extend(
        f"{domain} {args.grommunio_mailu_nexthop}"
        for domain in domains
    )

    write_map(Path(args.mailu_transport_out), mailu_transport_lines)
    write_map(Path(args.mailu_valid_out), mailu_valid_lines)
    write_map(Path(args.grommunio_transport_out), grommunio_transport_lines)

    print(f"Inventory:            {inventory_path}")
    print(f"Mailu transport map:  {args.mailu_transport_out}")
    print(f"Mailu valid map:      {args.mailu_valid_out}")
    print(f"grommunio transport:  {args.grommunio_transport_out}")
    print("Recipients:")
    for address in addresses:
        print(f"  {address}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
