"""Semantic checks that a template engine cannot catch.

A config can be syntactically perfect and still take a site down: an access
port pointing at a VLAN that was never created drops into a black hole, and two
SVIs claiming the same gateway address cause ARP flapping that looks like an
intermittent Wi-Fi problem for days. These rules encode those failure modes.

Checks are split by scope. Per-device rules see one switch; fleet rules see the
whole inventory, because duplicate addressing only exists *between* devices.
"""

from __future__ import annotations

import ipaddress
from collections import defaultdict
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Iterable

from .inventory import Device


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class Issue:
    severity: Severity
    device: str
    message: str


DeviceRule = Callable[[Device], Iterable[Issue]]
FleetRule = Callable[[list[Device]], Iterable[Issue]]

_device_rules: list[DeviceRule] = []
_fleet_rules: list[FleetRule] = []


def device_rule(fn: DeviceRule) -> DeviceRule:
    _device_rules.append(fn)
    return fn


def fleet_rule(fn: FleetRule) -> FleetRule:
    _fleet_rules.append(fn)
    return fn


def _err(dev: Device, msg: str) -> Issue:
    return Issue(Severity.ERROR, dev.hostname, msg)


def _warn(dev: Device, msg: str) -> Issue:
    return Issue(Severity.WARNING, dev.hostname, msg)


def _addr(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """Return the IP part of a CIDR string, or None when it does not parse.

    Fleet rules iterate every address in the inventory; a malformed one must not
    take the whole validate run down with a traceback — the per-device rules
    already reported it, so here it is simply skipped.
    """
    try:
        return ipaddress.ip_interface(value).ip
    except ValueError:
        return None


# Cisco reserves 1002-1005 for legacy FDDI/Token Ring; "vlan 1003" is rejected
# on IOS but accepted on VRP, so the same inventory can fail on half a fleet.
_IOS_RESERVED = range(1002, 1006)


@device_rule
def vlan_ids_valid(dev: Device) -> Iterable[Issue]:
    seen: set[int] = set()
    for vlan in dev.vlans:
        if not 1 <= vlan.id <= 4094:
            yield _err(dev, f"VLAN {vlan.id} is outside 1-4094")
        if vlan.id in seen:
            yield _err(dev, f"VLAN {vlan.id} is defined twice")
        if dev.platform == "cisco_ios" and vlan.id in _IOS_RESERVED:
            yield _err(dev, f"VLAN {vlan.id} is reserved on Cisco IOS")
        seen.add(vlan.id)


@device_rule
def ports_reference_existing_vlans(dev: Device) -> Iterable[Issue]:
    defined = {v.id for v in dev.vlans}
    for intf in dev.interfaces:
        if intf.mode == "access":
            if intf.vlan is None:
                yield _err(dev, f"{intf.name}: access port without a VLAN")
            elif intf.vlan not in defined:
                yield _err(dev, f"{intf.name}: access VLAN {intf.vlan} is not defined")
        elif intf.mode == "trunk":
            if not intf.allowed_vlans:
                yield _err(dev, f"{intf.name}: trunk with an empty allow-list")
            missing = sorted(set(intf.allowed_vlans) - defined)
            if missing:
                yield _err(dev, f"{intf.name}: trunk allows undefined VLANs {missing}")
            if intf.native_vlan is not None and intf.native_vlan in intf.allowed_vlans:
                # Untagged traffic on a VLAN that is also tagged elsewhere is how
                # double-tagging (VLAN hopping) attacks reach other segments.
                yield _warn(
                    dev,
                    f"{intf.name}: native VLAN {intf.native_vlan} is also carried "
                    "tagged; use a dedicated unused native VLAN",
                )


@device_rule
def unique_interface_names(dev: Device) -> Iterable[Issue]:
    counts: dict[str, int] = defaultdict(int)
    for intf in dev.interfaces:
        counts[intf.name.lower()] += 1
    for name, n in counts.items():
        if n > 1:
            yield _err(dev, f"interface {name} is configured {n} times")


@device_rule
def svis_and_routing(dev: Device) -> Iterable[Issue]:
    defined = {v.id for v in dev.vlans}
    networks: list[tuple[str, ipaddress.IPv4Network | ipaddress.IPv6Network]] = []

    for svi in dev.svis:
        if svi.vlan not in defined:
            yield _err(dev, f"SVI for VLAN {svi.vlan} but the VLAN is not defined")
        try:
            iface = ipaddress.ip_interface(svi.ip)
        except ValueError:
            yield _err(dev, f"SVI {svi.vlan}: '{svi.ip}' is not a valid CIDR address")
            continue
        if iface.ip in (iface.network.network_address, iface.network.broadcast_address):
            if iface.network.prefixlen < 31:
                yield _err(dev, f"SVI {svi.vlan}: {svi.ip} is a network/broadcast address")
        networks.append((f"Vlan{svi.vlan}", iface.network))

    for intf in dev.interfaces:
        if intf.mode == "routed":
            if not intf.ip:
                yield _err(dev, f"{intf.name}: routed port without an IP")
                continue
            if "/" not in intf.ip:
                # A bare address is silently read as /32, which renders a
                # 255.255.255.255 mask and routes nothing. Require the intent.
                yield _err(dev, f"{intf.name}: '{intf.ip}' needs a prefix length, e.g. {intf.ip}/24")
                continue
            try:
                iface = ipaddress.ip_interface(intf.ip)
            except ValueError:
                yield _err(dev, f"{intf.name}: '{intf.ip}' is not a valid CIDR address")
                continue
            networks.append((intf.name, iface.network))

    # Overlapping connected subnets make the RIB pick one interface and silently
    # blackhole the other; both IOS and VRP will refuse only the exact-duplicate case.
    for i, (name_a, net_a) in enumerate(networks):
        for name_b, net_b in networks[i + 1:]:
            if net_a.overlaps(net_b):
                yield _err(dev, f"{name_a} ({net_a}) overlaps {name_b} ({net_b})")

    if dev.default_gateway:
        try:
            gw = ipaddress.ip_address(dev.default_gateway)
        except ValueError:
            yield _err(dev, f"default gateway '{dev.default_gateway}' is not a valid IP address")
        else:
            if networks and not any(gw in net for _, net in networks):
                yield _err(
                    dev,
                    f"default gateway {gw} is not inside any connected subnet, "
                    "so it can never be resolved via ARP",
                )


@fleet_rule
def unique_addresses(devices: list[Device]) -> Iterable[Issue]:
    # A claimant is (hostname, kind, label). The kind is what makes the rule
    # decidable: "the same device owns this twice, so it must be fine" is too
    # lax, because a switch with two SVIs on the same address is a real loop,
    # while a switch managing itself on one SVI is genuinely normal.
    owners: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    for dev in devices:
        owners[dev.host].append((dev.hostname, "mgmt", "mgmt"))
        for svi in dev.svis:
            addr = _addr(svi.ip)
            if addr is not None:
                owners[str(addr)].append((dev.hostname, "svi", f"Vlan{svi.vlan}"))
        for intf in dev.interfaces:
            if intf.mode == "routed" and intf.ip:
                addr = _addr(intf.ip)
                if addr is not None:
                    owners[str(addr)].append((dev.hostname, "routed", intf.name))

    for addr, claims in owners.items():
        if len(claims) < 2:
            continue
        # The single legitimate repetition: one device, one management address,
        # and exactly one SVI carrying it. Anything else is a duplicate that
        # will flap ARP or blackhole traffic somewhere.
        hosts = {c[0] for c in claims}
        kinds = sorted(c[1] for c in claims)
        if len(hosts) == 1 and kinds == ["mgmt", "svi"] and len(claims) == 2:
            continue
        labels = [f"{host} ({label})" if kind == "mgmt" else f"{host} {label}"
                  for host, kind, label in claims]
        for dev in devices:
            if dev.hostname in hosts:
                yield Issue(
                    Severity.ERROR,
                    dev.hostname,
                    f"address {addr} is also used by: {', '.join(labels)}",
                )
                break


@fleet_rule
def unique_hostnames(devices: list[Device]) -> Iterable[Issue]:
    seen: set[str] = set()
    for dev in devices:
        if dev.hostname in seen:
            yield Issue(Severity.ERROR, dev.hostname, "hostname is used by more than one device")
        seen.add(dev.hostname)


def validate(devices: list[Device]) -> list[Issue]:
    issues: list[Issue] = []
    for dev in devices:
        for rule in _device_rules:
            issues.extend(rule(dev))
    for fleet in _fleet_rules:
        issues.extend(fleet(devices))
    return issues
