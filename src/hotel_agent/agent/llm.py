"""Provider-neutral LLM interface (ADR-0023).

`complete_json` mirrors what any JSON-mode chat API offers: a system prompt, a user
prompt and a JSON schema; it returns raw text. Callers validate the text themselves, so a
model can never hand back an object that skipped validation. No vendor SDK is imported.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, Protocol


class LLMError(RuntimeError):
    pass


class LLMClient(Protocol):
    @property
    def name(self) -> str: ...

    def complete_json(self, system: str, user: str, json_schema: dict[str, Any]) -> str: ...


class ScriptedLLM:
    """Test double that returns whatever it is told — including hostile or invalid output."""

    def __init__(self, reply: str | dict[str, Any] | Callable[[str], str | dict[str, Any]]) -> None:
        self._reply = reply
        self.prompts: list[tuple[str, str]] = []

    @property
    def name(self) -> str:
        return "scripted-llm"

    def complete_json(self, system: str, user: str, json_schema: dict[str, Any]) -> str:
        self.prompts.append((system, user))
        reply = self._reply(user) if callable(self._reply) else self._reply
        return reply if isinstance(reply, str) else json.dumps(reply)
