"""Command-line interface.

The commands map onto a deliberate change workflow — validate, render, diff,
then push — and ``push`` refuses to run if validation finds errors. Making the
safe path the default path matters more than any single check.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from . import __version__
from .devices import fetch_running, push_config, run_parallel
from .diff import diff_configs, format_changes
from .inventory import Device, InventoryError, load_inventory
from .render import build_environment, render_device
from .validate import Severity, validate

app = typer.Typer(add_completion=False, help="Network intent compiler for Cisco IOS and Huawei VRP.")
console = Console()

InventoryOpt = typer.Option(Path("inventory.yml"), "--inventory", "-i", help="YAML inventory file")
LimitOpt = typer.Option(None, "--limit", "-l", help="Only these hostnames (repeatable)")
TemplatesOpt = typer.Option(None, "--templates", "-t", help="Extra template directory")


def _load(path: Path, limit: Optional[list[str]]) -> list[Device]:
    try:
        devices = load_inventory(path)
    except InventoryError as exc:
        console.print(f"[red]inventory error:[/] {exc}")
        raise typer.Exit(2)
    if limit:
        unknown = set(limit) - {d.hostname for d in devices}
        if unknown:
            console.print(f"[red]unknown hosts:[/] {', '.join(sorted(unknown))}")
            raise typer.Exit(2)
        devices = [d for d in devices if d.hostname in limit]
    return devices


def _report(devices: list[Device], all_devices: list[Device]) -> bool:
    # Fleet rules need the whole inventory even when --limit narrows the
    # target, otherwise a duplicate IP on an excluded switch goes unnoticed.
    targets = {d.hostname for d in devices}
    issues = [i for i in validate(all_devices) if i.device in targets]
    if not issues:
        console.print(f"[green]✓[/] {len(devices)} device(s) passed validation")
        return True
    table = Table("severity", "device", "issue", show_lines=False)
    for issue in issues:
        colour = "red" if issue.severity is Severity.ERROR else "yellow"
        table.add_row(f"[{colour}]{issue.severity.value}[/]", issue.device, issue.message)
    console.print(table)
    return not any(i.severity is Severity.ERROR for i in issues)


@app.command("validate")
def validate_cmd(inventory: Path = InventoryOpt, limit: Optional[list[str]] = LimitOpt) -> None:
    """Check the inventory for addressing, VLAN and topology mistakes."""
    devices = _load(inventory, limit)
    if not _report(devices, load_inventory(inventory)):
        raise typer.Exit(1)


@app.command()
def render(
    inventory: Path = InventoryOpt,
    out: Path = typer.Option(Path("build"), "--out", "-o", help="Output directory"),
    limit: Optional[list[str]] = LimitOpt,
    templates: Optional[Path] = TemplatesOpt,
) -> None:
    """Compile intended configs into the output directory."""
    devices = _load(inventory, limit)
    env = build_environment([templates] if templates else None)
    out.mkdir(parents=True, exist_ok=True)
    for dev in devices:
        target = out / f"{dev.hostname}.cfg"
        target.write_text(render_device(dev, env), encoding="utf-8")
        console.print(f"[cyan]rendered[/] {target}")


@app.command()
def diff(
    inventory: Path = InventoryOpt,
    running_dir: Path = typer.Option(Path("backups"), "--running", "-r", help="Directory of running configs"),
    limit: Optional[list[str]] = LimitOpt,
    templates: Optional[Path] = TemplatesOpt,
) -> None:
    """Show what would change on each device, section by section."""
    devices = _load(inventory, limit)
    env = build_environment([templates] if templates else None)
    drift = False
    for dev in devices:
        running_file = running_dir / f"{dev.hostname}.cfg"
        if not running_file.exists():
            console.print(f"[yellow]{dev.hostname}: no backup at {running_file}, run 'strata backup' first[/]")
            continue
        changes = diff_configs(running_file.read_text(encoding="utf-8"), render_device(dev, env))
        drift |= bool(changes)
        console.rule(dev.hostname)
        console.print(format_changes(changes), highlight=False, markup=False)
    # Non-zero on drift lets a scheduled job alert when someone hand-edits a switch.
    raise typer.Exit(3 if drift else 0)


@app.command()
def backup(
    inventory: Path = InventoryOpt,
    out: Path = typer.Option(Path("backups"), "--out", "-o"),
    limit: Optional[list[str]] = LimitOpt,
    workers: int = typer.Option(8, help="Parallel SSH sessions"),
) -> None:
    """Pull running configs from devices over SSH."""
    devices = _load(inventory, limit)
    out.mkdir(parents=True, exist_ok=True)
    by_name = {d.hostname: d for d in devices}
    failed = False
    for res in run_parallel(devices, fetch_running, workers):
        if res.ok:
            (out / f"{res.hostname}.cfg").write_text(res.output, encoding="utf-8")
            console.print(f"[green]✓[/] {res.hostname} ({by_name[res.hostname].host})")
        else:
            failed = True
            console.print(f"[red]✗[/] {res.hostname}: {res.output}")
    if failed:
        raise typer.Exit(1)


@app.command()
def push(
    inventory: Path = InventoryOpt,
    limit: Optional[list[str]] = LimitOpt,
    templates: Optional[Path] = TemplatesOpt,
    apply: bool = typer.Option(False, "--apply", help="Actually send commands (default is a dry run)"),
    no_save: bool = typer.Option(False, "--no-save", help="Do not write config to startup"),
    workers: int = typer.Option(4, help="Parallel SSH sessions"),
) -> None:
    """Deploy intended configs. Dry run unless --apply is given."""
    devices = _load(inventory, limit)
    if not _report(devices, load_inventory(inventory)):
        console.print("[red]refusing to push: fix validation errors first[/]")
        raise typer.Exit(1)

    env = build_environment([templates] if templates else None)
    rendered = {d.hostname: render_device(d, env) for d in devices}

    if not apply:
        for dev in devices:
            console.rule(f"{dev.hostname} (dry run)")
            console.print(rendered[dev.hostname], highlight=False, markup=False)
        console.print("[yellow]dry run only — re-run with --apply to deploy[/]")
        return

    def task(dev: Device) -> str:
        return push_config(dev, rendered[dev.hostname], save=not no_save)

    failed = False
    for res in run_parallel(devices, task, workers):
        mark = "[green]✓[/]" if res.ok else "[red]✗[/]"
        failed |= not res.ok
        console.print(f"{mark} {res.hostname}" + ("" if res.ok else f": {res.output}"))
    if failed:
        raise typer.Exit(1)


@app.command()
def version() -> None:
    console.print(__version__)


if __name__ == "__main__":
    app()
