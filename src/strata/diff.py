"""Structure-aware config diff.

A plain ``difflib`` diff of two running configs is mostly noise: the device
reorders sections, injects timestamps and ``!`` separators, and expands
defaults you never wrote. Worse, a line-based diff loses context — ``- shutdown``
means nothing unless you know which interface it belonged to.

Both IOS and VRP express hierarchy through indentation, so we parse each
config into a tree of ``parent -> children`` and compare sets per section.
That makes the result order-insensitive and tells you *where* a change lives,
which is the question an operator actually has before pressing enter.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Lines the device generates on its own. Including them would make every
# backup look "changed" and train people to ignore the diff.
_NOISE = [
    re.compile(r"^\s*!.*$"),
    re.compile(r"^\s*#\s*$"),
    re.compile(r"^Building configuration"),
    re.compile(r"^Current configuration"),
    re.compile(r"^! (Last configuration change|NVRAM config last updated)"),
    re.compile(r"^ntp clock-period"),
    re.compile(r"^!Software Version"),
    re.compile(r"^\s*(end|return)\s*$"),
]


@dataclass
class Section:
    line: str
    children: list["Section"] = field(default_factory=list)


@dataclass
class Change:
    path: tuple[str, ...]
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)


def _clean(text: str) -> list[str]:
    lines = []
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip() or any(p.match(line) for p in _NOISE):
            continue
        lines.append(line)
    return lines


def parse(text: str) -> list[Section]:
    root = Section("<root>")
    # A stack of (indent, section) lets us attach each line to the nearest
    # less-indented ancestor in one pass, regardless of nesting depth.
    stack: list[tuple[int, Section]] = [(-1, root)]
    for line in _clean(text):
        indent = len(line) - len(line.lstrip(" "))
        node = Section(line.strip())
        while stack[-1][0] >= indent:
            stack.pop()
        stack[-1][1].children.append(node)
        stack.append((indent, node))
    return root.children


def _compare(path: tuple[str, ...], old: list[Section], new: list[Section], out: list[Change]) -> None:
    old_map = {s.line: s for s in old}
    new_map = {s.line: s for s in new}
    change = Change(path)
    change.removed = [k for k in old_map if k not in new_map]
    change.added = [k for k in new_map if k not in old_map]
    if change.added or change.removed:
        out.append(change)
    for key in old_map.keys() & new_map.keys():
        if old_map[key].children or new_map[key].children:
            _compare(path + (key,), old_map[key].children, new_map[key].children, out)


def diff_configs(running: str, intended: str) -> list[Change]:
    changes: list[Change] = []
    _compare((), parse(running), parse(intended), changes)
    # Deterministic ordering keeps CI output stable between runs.
    changes.sort(key=lambda c: c.path)
    return changes


def format_changes(changes: list[Change]) -> str:
    if not changes:
        return "no changes"
    out = []
    for c in changes:
        header = " > ".join(c.path) if c.path else "(global)"
        out.append(f"[{header}]")
        out.extend(f"  - {line}" for line in c.removed)
        out.extend(f"  + {line}" for line in c.added)
    return "\n".join(out)
