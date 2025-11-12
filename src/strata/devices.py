"""Device I/O over SSH.

Netmiko is an optional dependency so that rendering, validation and diffing —
the parts you run in CI on every commit — install with zero native deps.

Credentials come only from the environment. Putting them in the inventory is
the fastest way to leak them, because inventories are the file people commit,
paste into tickets and share with vendors.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Callable, Protocol

from .inventory import Device

# Netmiko device_type names differ from our platform keys for VRP.
_NETMIKO_TYPE = {"cisco_ios": "cisco_ios", "huawei_vrp": "huawei"}

_SHOW_RUNNING = {
    "cisco_ios": "show running-config",
    "huawei_vrp": "display current-configuration",
}


class Connection(Protocol):
    def send_command(self, command: str, **kwargs) -> str: ...
    def send_config_set(self, commands: list[str], **kwargs) -> str: ...
    def save_config(self, *args, **kwargs) -> str: ...
    def disconnect(self) -> None: ...


Connector = Callable[[Device], Connection]


@dataclass
class Result:
    hostname: str
    ok: bool
    output: str


def netmiko_connector(device: Device) -> Connection:
    try:
        from netmiko import ConnectHandler
    except ImportError as exc:
        raise RuntimeError("SSH support needs netmiko: pip install 'strata[ssh]'") from exc

    username = os.environ.get("STRATA_USERNAME")
    password = os.environ.get("STRATA_PASSWORD")
    if not username or not password:
        raise RuntimeError("set STRATA_USERNAME and STRATA_PASSWORD")

    return ConnectHandler(
        device_type=_NETMIKO_TYPE[device.platform],
        host=device.host,
        username=username,
        password=password,
        secret=os.environ.get("STRATA_ENABLE_SECRET", ""),
        # Access switches behind slow WAN links regularly exceed the 10 s
        # default while paging a large running-config.
        read_timeout_override=60,
    )


def _config_lines(rendered: str) -> list[str]:
    # The rendered file is a full config for humans; the device wants only the
    # commands. Separators and the terminating end/return would exit config mode
    # early on IOS and drop VRP back to user view mid-push.
    skip = {"!", "#", "end", "return"}
    return [ln.rstrip() for ln in rendered.splitlines() if ln.strip() and ln.strip() not in skip]


def fetch_running(device: Device, connect: Connector = netmiko_connector) -> str:
    conn = connect(device)
    try:
        return conn.send_command(_SHOW_RUNNING[device.platform])
    finally:
        conn.disconnect()


def push_config(
    device: Device,
    rendered: str,
    connect: Connector = netmiko_connector,
    save: bool = True,
) -> str:
    conn = connect(device)
    try:
        output = conn.send_config_set(_config_lines(rendered))
        if save:
            # Without saving, the change survives only until the next reboot —
            # often a power cut weeks later, when nobody remembers the change.
            output += "\n" + conn.save_config()
        return output
    finally:
        conn.disconnect()


def run_parallel(
    devices: list[Device],
    task: Callable[[Device], str],
    workers: int = 8,
) -> list[Result]:
    # SSH is I/O-bound, so threads are enough; the cap protects AAA servers,
    # which commonly rate-limit or lock accounts on a burst of logins.
    results: list[Result] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(task, d): d for d in devices}
        for fut in as_completed(futures):
            dev = futures[fut]
            try:
                results.append(Result(dev.hostname, True, fut.result()))
            except Exception as exc:  # one bad device must not abort the batch
                results.append(Result(dev.hostname, False, str(exc)))
    return sorted(results, key=lambda r: r.hostname)
