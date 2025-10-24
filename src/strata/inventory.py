"""Inventory model.

Precedence is ``defaults < group < device``, mirroring how Ansible resolves
host/group vars, so the file reads naturally to anyone coming from that world.
Parsing is strict on purpose: a typo in a VLAN key that silently becomes
``None`` would render a valid-looking config that drops a whole subnet.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

SUPPORTED_PLATFORMS = ("cisco_ios", "huawei_vrp")
INTERFACE_MODES = ("access", "trunk", "routed", "shutdown")


class InventoryError(ValueError):
    pass


@dataclass
class Vlan:
    id: int
    name: str


@dataclass
class Interface:
    name: str
    mode: str = "access"
    description: str = ""
    vlan: int | None = None
    allowed_vlans: list[int] = field(default_factory=list)
    native_vlan: int | None = None
    ip: str | None = None
    poe: bool = False


@dataclass
class Svi:
    vlan: int
    ip: str
    description: str = ""
    dhcp_relay: str | None = None


@dataclass
class Device:
    hostname: str
    platform: str
    host: str
    group: str | None = None
    vlans: list[Vlan] = field(default_factory=list)
    interfaces: list[Interface] = field(default_factory=list)
    svis: list[Svi] = field(default_factory=list)
    default_gateway: str | None = None
    vars: dict[str, Any] = field(default_factory=dict)


def _deep_merge(base: dict, override: dict) -> dict:
    # Lists replace rather than concatenate. Appending would make it impossible
    # for a device to *remove* a VLAN its group defines, and order-dependent
    # list merging is the classic source of "where did VLAN 99 come from".
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _parse_vlans(raw: list[dict], where: str) -> list[Vlan]:
    vlans = []
    for item in raw or []:
        if "id" not in item:
            raise InventoryError(f"{where}: every VLAN needs an 'id'")
        vlans.append(Vlan(id=int(item["id"]), name=str(item.get("name", f"VLAN{item['id']}"))))
    return vlans


def _parse_interfaces(raw: list[dict], where: str) -> list[Interface]:
    interfaces = []
    for item in raw or []:
        if "name" not in item:
            raise InventoryError(f"{where}: every interface needs a 'name'")
        mode = item.get("mode", "access")
        if mode not in INTERFACE_MODES:
            raise InventoryError(
                f"{where}: interface {item['name']} has unknown mode '{mode}' "
                f"(use one of {', '.join(INTERFACE_MODES)})"
            )
        interfaces.append(
            Interface(
                name=str(item["name"]),
                mode=mode,
                description=str(item.get("description", "")),
                vlan=item.get("vlan"),
                allowed_vlans=[int(v) for v in item.get("allowed_vlans", [])],
                native_vlan=item.get("native_vlan"),
                ip=item.get("ip"),
                poe=bool(item.get("poe", False)),
            )
        )
    return interfaces


def _parse_svis(raw: list[dict], where: str) -> list[Svi]:
    svis = []
    for item in raw or []:
        if "vlan" not in item or "ip" not in item:
            raise InventoryError(f"{where}: every SVI needs 'vlan' and 'ip'")
        svis.append(
            Svi(
                vlan=int(item["vlan"]),
                ip=str(item["ip"]),
                description=str(item.get("description", "")),
                dhcp_relay=item.get("dhcp_relay"),
            )
        )
    return svis


def parse_inventory(data: dict) -> list[Device]:
    if not isinstance(data, dict) or "devices" not in data:
        raise InventoryError("inventory must be a mapping with a 'devices' list")

    defaults = data.get("defaults", {}) or {}
    groups = data.get("groups", {}) or {}
    devices: list[Device] = []

    for index, raw in enumerate(data["devices"]):
        where = f"devices[{index}]"
        if "hostname" not in raw:
            raise InventoryError(f"{where}: missing 'hostname'")
        where = raw["hostname"]

        group_name = raw.get("group")
        if group_name and group_name not in groups:
            raise InventoryError(f"{where}: group '{group_name}' is not defined")

        merged = _deep_merge(defaults, groups.get(group_name, {}) if group_name else {})
        merged = _deep_merge(merged, raw)

        platform = merged.get("platform")
        if platform not in SUPPORTED_PLATFORMS:
            raise InventoryError(
                f"{where}: platform '{platform}' is not supported "
                f"(use one of {', '.join(SUPPORTED_PLATFORMS)})"
            )
        if "host" not in merged:
            raise InventoryError(f"{where}: missing 'host' (management IP)")

        known = {
            "hostname", "platform", "host", "group", "vlans",
            "interfaces", "svis", "default_gateway",
        }
        devices.append(
            Device(
                hostname=str(merged["hostname"]),
                platform=platform,
                host=str(merged["host"]),
                group=group_name,
                vlans=_parse_vlans(merged.get("vlans"), where),
                interfaces=_parse_interfaces(merged.get("interfaces"), where),
                svis=_parse_svis(merged.get("svis"), where),
                default_gateway=merged.get("default_gateway"),
                vars={k: v for k, v in merged.items() if k not in known},
            )
        )

    return devices


def load_inventory(path: str | Path) -> list[Device]:
    path = Path(path)
    if not path.exists():
        raise InventoryError(f"inventory file not found: {path}")
    with path.open(encoding="utf-8") as fh:
        try:
            # safe_load: inventories get shared and copied around; full_load
            # would execute arbitrary Python tags embedded in the YAML.
            data = yaml.safe_load(fh)
        except yaml.YAMLError as exc:
            raise InventoryError(f"{path}: invalid YAML ({exc})") from exc
    return parse_inventory(data)
