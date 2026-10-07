"""Repository hygiene checks."""

from __future__ import annotations

import re

from hotel_agent.config import PROJECT_ROOT

# Tabs/newlines/carriage returns are fine; other control characters (e.g. a stray
# backspace where a regex "\b" was intended) are invisible bugs.
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def test_no_control_characters_in_source_or_docs() -> None:
    offenders = []
    for folder in ("src", "tests", "migrations", "docs", "data"):
        for path in (PROJECT_ROOT / folder).rglob("*"):
            if path.suffix in {".py", ".md", ".json"} and path.is_file():
                text = path.read_text(encoding="utf-8")
                for lineno, line in enumerate(text.splitlines(), 1):
                    if _CONTROL.search(line):
                        offenders.append(f"{path.relative_to(PROJECT_ROOT)}:{lineno}")
    assert offenders == []
