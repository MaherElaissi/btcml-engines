"""Roadmap progress parsed from TODO.md: one entry per "## " section, counting its checkboxes."""

from __future__ import annotations

import re
from pathlib import Path

ITEM = re.compile(r"^- \[([ xX])\] (.*)$")
GROUP = re.compile(r"^\*\*(.+?)\*\*")


def parse_roadmap(text: str) -> list[dict]:
    sections: list[dict] = []
    group = None
    item = None
    for line in text.splitlines():
        if line.startswith("## "):
            sections.append({"title": line[3:].strip(), "items": []})
            group = item = None
        elif not sections:
            continue
        elif m := ITEM.match(line):
            item = {"text": m[2].strip(), "done": m[1] != " ", "group": group}
            sections[-1]["items"].append(item)
        elif item is not None and line.startswith("  ") and line.strip():
            item["text"] += " " + line.strip()  # wrapped continuation line
        else:
            item = None
            if m := GROUP.match(line):
                group = m[1].strip()

    for s in sections:
        s["done"] = sum(i["done"] for i in s["items"])
        s["total"] = len(s["items"])
        s["status"] = (
            "done" if s["total"] and s["done"] == s["total"]
            else "in_progress" if s["done"] else "not_started"
        )
    return [s for s in sections if s["total"]]


def roadmap(root: Path) -> list[dict]:
    path = root / "TODO.md"
    if not path.exists():
        raise FileNotFoundError(f"No roadmap at {path}")
    return parse_roadmap(path.read_text(encoding="utf-8"))
