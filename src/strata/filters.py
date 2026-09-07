"""Address filters exposed to Jinja2 templates.

The inventory stores every L3 address as a single CIDR string (``10.0.10.1/24``)
so a device and its subnet can never drift apart. Templates then project that one
value into whatever notation the vendor CLI expects: IOS wants dotted masks,
VRP accepts prefix lengths, and ACL/OSPF statements want inverse masks.
"""

from __future__ import annotations

import ipaddress

# ip_interface (not ip_network) is deliberate: ip_network("10.0.10.1/24") raises
# because host bits are set, but an interface address *must* have host bits.
_iface = ipaddress.ip_interface


def ip_only(cidr: str) -> str:
    return str(_iface(cidr).ip)


def netmask(cidr: str) -> str:
    return str(_iface(cidr).network.netmask)


def prefixlen(cidr: str) -> int:
    return _iface(cidr).network.prefixlen


def wildcard(cidr: str) -> str:
    # A wildcard is the bitwise inverse of the mask, not "mask with 0 and 255
    # swapped" — /22 yields 0.0.3.255. Hand-written ACLs get this wrong often.
    return str(_iface(cidr).network.hostmask)


def network(cidr: str) -> str:
    return str(_iface(cidr).network.network_address)


def vlan_ranges(vlans: list[int]) -> str:
    """Collapse VLAN IDs into range syntax for trunk allow-lists.

    Both IOS and VRP cap a single command line in length, and long comma lists
    on large trunks silently truncate on some images. Emitting ``10-12,20``
    keeps the line short and makes the rendered diff readable.
    """
    if not vlans:
        return ""
    ordered = sorted(set(vlans))
    runs: list[tuple[int, int]] = []
    start = prev = ordered[0]
    for vid in ordered[1:]:
        if vid != prev + 1:
            runs.append((start, prev))
            start = vid
        prev = vid
    runs.append((start, prev))
    return ",".join(str(a) if a == b else f"{a}-{b}" for a, b in runs)


FILTERS = {
    "ip_only": ip_only,
    "netmask": netmask,
    "prefixlen": prefixlen,
    "wildcard": wildcard,
    "network": network,
    "vlan_ranges": vlan_ranges,
}
