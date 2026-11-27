"""Open the Epic and one GitHub issue per ticket in tickets/tickets_*.md, in work order, and link them.

    uv run python tickets/create_issues.py            # dry run: the Epic, then every ticket in work order
    uv run python tickets/create_issues.py --create   # labels, Epic, tickets, then cross-links

A ticket starts at "## [ID] · Title" and ends at the next "---" or "## Needs confirmation". Its labels are the
"**Label:**" line plus EXTRA_LABELS. After every issue exists, each ticket body is edited once:
- "Refs #TBD" becomes "Refs #<Epic>", so the ticket points at its Epic.
- Every ticket ID mentioned (for example "APP-03") gains its issue number.
The Epic body gains a checklist of the tickets in work order. Re-running skips any issue whose title already exists.
The mapping is written to tickets/issue-map.txt. Needs `gh` authenticated; run from the repository root.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
FILES = ("tickets_app.md", "tickets_ci.md", "tickets_web.md")
EPIC_FILE = HERE / "EPIC-demo-interface.md"
EXTRA_LABELS = ("Interface", "UI-UX")
WORK_ORDER = (
    # Phase 0 - live backend and contract
    "APP-01",
    "APP-02",
    "APP-03",
    "APP-04",
    "APP-05",
    "APP-06",
    "APP-07",
    "CI-02",
    # Phase 1 - walking skeleton
    "WEB-01",
    "CI-01",
    "WEB-02",
    "WEB-03",
    "WEB-04",
    "WEB-05",
    "APP-08",
    # Phase 2 - screens
    "WEB-06",
    "WEB-07",
    "WEB-08",
    "WEB-09",
    "WEB-10",
    "WEB-11",
    "APP-09",
    # Phase 3 - hardening and demo
    "WEB-12",
    "WEB-13",
    "CI-03",
    "WEB-14",
    "APP-10",
)
PHASES = {
    "APP-01": "Phase 0 — live backend and contract",
    "WEB-01": "Phase 1 — walking skeleton",
    "WEB-06": "Phase 2 — screens",
    "WEB-12": "Phase 3 — hardening and demo",
}
HEADING = re.compile(r"^## \[(?P<id>[A-Z]+-\d+)\] · (?P<title>.+)$")
ID_REF = re.compile(r"\b(?:APP|WEB|CI|ENG|RES)-\d{2}\b")
COLORS = {
    "app": "0e8a16",
    "web": "1d76db",
    "ci": "fbca04",
    "feature": "a2eeef",
    "fix": "d73a4a",
    "refactor": "cfd3d7",
    "schema": "5319e7",
    "security": "b60205",
    "test": "c5def5",
    "docs": "0075ca",
    "Interface": "7057ff",
    "UI-UX": "e99695",
}


def parse() -> dict[str, dict[str, object]]:
    """Every ticket in the side files, keyed by its ID, with its title, labels and body."""
    tickets: dict[str, dict[str, object]] = {}
    for name in FILES:
        current: dict[str, object] | None = None
        for line in (HERE / name).read_text(encoding="utf-8").splitlines():
            match = HEADING.match(line)
            if match:
                current = {"id": match["id"], "title": f"[{match['id']}] {match['title']}", "lines": []}
                tickets[match["id"]] = current
            elif current is not None and (line == "---" or line.startswith("## Needs confirmation")):
                current = None
            elif current is not None:
                current["lines"].append(line)  # type: ignore[union-attr]
    for ticket in tickets.values():
        body = "\n".join(ticket.pop("lines")).strip() + "\n"  # type: ignore[arg-type]
        label_line = next(line for line in body.splitlines() if line.startswith("**Label:**"))
        side_and_type = [label.strip() for label in label_line.removeprefix("**Label:**").split(",")]
        ticket["labels"] = [*side_and_type, *EXTRA_LABELS]
        ticket["body"] = body
    missing = set(WORK_ORDER) ^ set(tickets)
    if missing:
        raise SystemExit(f"work order and ticket files disagree: {sorted(missing)}")
    return tickets


def epic() -> tuple[str, str]:
    """The Epic's title (its first heading) and body."""
    text = EPIC_FILE.read_text(encoding="utf-8")
    first, _, rest = text.partition("\n")
    return first.removeprefix("# ").strip(), rest.strip() + "\n"


def gh(*arguments: str, body: str | None = None) -> str:
    """Run gh; a body goes through a temporary file (Windows caps a command line at 32,767 characters)."""
    if body is None:
        return _run(arguments)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".md", delete=False) as handle:
        handle.write(body)
    try:
        return _run((*arguments, "--body-file", handle.name))
    finally:
        Path(handle.name).unlink(missing_ok=True)


def _run(arguments: tuple[str, ...]) -> str:
    completed = subprocess.run(["gh", *arguments], capture_output=True, text=True, encoding="utf-8")
    if completed.returncode != 0:
        raise SystemExit(f"gh {' '.join(arguments[:2])} failed: {completed.stderr.strip()}")
    return completed.stdout.strip()


def main(argv: list[str]) -> int:
    """Dry run by default; with ``--create``, open and link every issue."""
    tickets = parse()
    epic_title, epic_body = epic()
    if "--create" not in argv:
        print(f"would create: [{','.join(EXTRA_LABELS)}] {epic_title}")
        for ticket_id in WORK_ORDER:
            ticket = tickets[ticket_id]
            print(f"would create: [{','.join(ticket['labels'])}] {ticket['title']}")  # type: ignore[arg-type]
        print(f"1 epic + {len(WORK_ORDER)} tickets")
        return 0

    existing_labels = {item["name"] for item in json.loads(gh("label", "list", "--limit", "300", "--json", "name"))}
    wanted = {label for ticket in tickets.values() for label in ticket["labels"]}  # type: ignore[union-attr]
    for label in sorted(wanted - existing_labels):
        gh("label", "create", label, "--color", COLORS.get(label, "ededed"))
        print(f"label created: {label}")

    existing = {
        item["title"]: str(item["number"])
        for item in json.loads(gh("issue", "list", "--state", "all", "--limit", "1000", "--json", "title,number"))
    }

    def create(title: str, labels: list[str], body: str) -> str:
        if title in existing:
            print(f"exists: #{existing[title]} {title}")
            return existing[title]
        url = gh("issue", "create", "--title", title, "--label", ",".join(labels), body=body)
        number = url.rsplit("/", 1)[-1]
        print(f"created: #{number} {title}")
        return number

    epic_number = create(epic_title, list(EXTRA_LABELS), epic_body)
    numbers = {
        ticket_id: create(
            str(tickets[ticket_id]["title"]),
            tickets[ticket_id]["labels"],  # type: ignore[arg-type]
            str(tickets[ticket_id]["body"]),
        )
        for ticket_id in WORK_ORDER
    }

    for ticket_id in WORK_ORDER:
        body = str(tickets[ticket_id]["body"]).replace("**Issue:** Refs #TBD", f"**Epic:** Refs #{epic_number}")
        body = ID_REF.sub(lambda m: f"{m[0]} (#{numbers[m[0]]})" if m[0] in numbers else m[0], body)
        gh("issue", "edit", numbers[ticket_id], body=body)

    checklist = ["", "## Tickets in work order", ""]
    for ticket_id in WORK_ORDER:
        if ticket_id in PHASES:
            checklist += ["", f"**{PHASES[ticket_id]}**", ""]
        checklist.append(f"- [ ] #{numbers[ticket_id]}")
    gh("issue", "edit", epic_number, body=epic_body + "\n".join(checklist) + "\n")

    lines = [f"EPIC #{epic_number}"] + [f"{ticket_id} #{numbers[ticket_id]}" for ticket_id in WORK_ORDER]
    (HERE / "issue-map.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"done: epic #{epic_number}, {len(numbers)} tickets; map in tickets/issue-map.txt")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
