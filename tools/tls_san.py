#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Discover local TLS SANs without importing stateful YOLOmux packages."""

from __future__ import annotations

import ipaddress
import json
import re
import shutil
import socket
import subprocess


def self_signed_interface_ips() -> tuple[str, ...]:
    addresses: list[str] = []

    def add(value: str, interface: str = "") -> None:
        if interface.startswith(("docker", "br-", "veth")):
            return
        candidate = value.split("%", 1)[0]
        try:
            parsed = ipaddress.ip_address(candidate)
        except ValueError:
            return
        if parsed.is_loopback or parsed.is_unspecified or candidate in addresses:
            return
        addresses.append(candidate)

    for family, target in (
        (socket.AF_INET, ("10.255.255.255", 1)),
        (socket.AF_INET6, ("2001:db8::1", 1, 0, 0)),
    ):
        try:
            with socket.socket(family, socket.SOCK_DGRAM) as probe:
                probe.connect(target)
                add(probe.getsockname()[0])
        except OSError:
            pass
    try:
        for iface_addr in socket.getaddrinfo(socket.gethostname(), None):
            add(iface_addr[4][0])
    except OSError:
        pass
    ip_command = shutil.which("ip")
    if ip_command:
        try:
            result = subprocess.run(
                [ip_command, "-j", "address", "show"],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for interface in json.loads(result.stdout):
                for addr_info in interface.get("addr_info", []):
                    add(str(addr_info.get("local", "")), str(interface.get("ifname", "")))
        except (OSError, subprocess.CalledProcessError, json.JSONDecodeError, TypeError):
            pass
    ifconfig_command = shutil.which("ifconfig")
    if ifconfig_command:
        try:
            result = subprocess.run(
                [ifconfig_command],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            interface = ""
            for line in result.stdout.splitlines():
                if line and not line[0].isspace():
                    interface = line.split(":", 1)[0]
                for match in re.finditer(r"\binet6?\s+(?:addr:)?([^\s]+)", line):
                    add(match.group(1), interface)
        except (OSError, subprocess.CalledProcessError):
            pass
    return tuple(addresses)


def self_signed_san(hostname: str, interface_ips: tuple[str, ...]) -> str:
    names = ["DNS:localhost", "IP:127.0.0.1"]
    if hostname and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*", hostname) and hostname != "localhost":
        names.append(f"DNS:{hostname}")
    for ip in interface_ips:
        entry = f"IP:{ip}"
        if entry not in names:
            names.append(entry)
    return ",".join(names)


def main() -> int:
    print(self_signed_san(socket.gethostname(), self_signed_interface_ips()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
