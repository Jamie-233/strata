"""Template rendering.

StrictUndefined is the important choice here. Jinja's default renders a missing
variable as an empty string, which for network config is the worst failure
mode: ``ip address {{ ip }}`` becomes ``ip address`` and the device rejects it
mid-push, leaving a half-applied change. Failing at render time keeps every
error on the operator's laptop instead of on the switch.
"""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from .filters import FILTERS
from .inventory import Device

BUILTIN_TEMPLATES = Path(__file__).parent / "templates"


def vrp_vlan_list(vlans: list[int]) -> str:
    # VRP's range grammar is "10 to 12 20", not IOS's "10-12,20". Feeding IOS
    # syntax to VRP doesn't error — it parses "10-12" as an unknown token and
    # ignores the rest of the line, so the trunk quietly carries nothing.
    from .filters import vlan_ranges

    return " ".join(
        part.replace("-", " to ") for part in vlan_ranges(vlans).split(",") if part
    )


def build_environment(extra_dirs: list[Path] | None = None) -> Environment:
    # User template dirs come first so a site can override a single vendor
    # template without forking the package.
    search_path = [str(p) for p in (extra_dirs or [])] + [str(BUILTIN_TEMPLATES)]
    env = Environment(
        loader=FileSystemLoader(search_path),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )
    env.filters.update(FILTERS)
    env.filters["vrp_vlan_list"] = vrp_vlan_list
    return env


def render_device(device: Device, env: Environment | None = None) -> str:
    env = env or build_environment()
    template = env.get_template(f"{device.platform}.j2")
    vlan_names = {v.id: v.name for v in device.vlans}
    return template.render(device=device, vars=device.vars, vlan_names=vlan_names)
