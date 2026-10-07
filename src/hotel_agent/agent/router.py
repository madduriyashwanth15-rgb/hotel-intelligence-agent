"""Routing: build the prompt, call the LLM, validate its structured reply.

Any failure (model error, invalid JSON, schema violation) becomes a REFUSAL — the model
can never cause something that its reply could not even describe. With a fallback model
configured (ADR-0025), such a failure is routed by the fallback instead, and the trace
records that the primary model failed.

The prompt carries only what routing needs: route definitions, tool names, descriptions
and argument types. No identity, role, hotel, database, approval or guard details.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from hotel_agent.agent.llm import LLMClient, LLMError
from hotel_agent.agent.schemas import Route, RouteDecision

REQUEST_MARKER = "REQUEST_JSON:"
MAX_RAW_REPLY_CHARS = 4000
GENERIC_CLARIFICATION = (
    "Could you say exactly what you need: a policy answer, a figure, or one booking, room or task?"
)

# Tuned on the dev set only (eval/agent_cases.json); see eval/results/llm_prompt_tuning.json.
# Kept in a text file so the tuned wording stays byte-for-byte unchanged.
PROMPT_VERSION = "router_prompt_v3"
SYSTEM_PROMPT = (
    Path(__file__).with_name(f"{PROMPT_VERSION}.txt").read_text(encoding="utf-8").rstrip("\n")
)


@dataclass(frozen=True, slots=True)
class ToolInfo:
    name: str
    description: str
    read_only: bool
    input_schema: dict[str, Any]


def _arg_type(spec: dict[str, Any], defs: dict[str, Any]) -> str:
    if "$ref" in spec:
        spec = defs.get(str(spec["$ref"]).rsplit("/", 1)[-1], {})
    if "anyOf" in spec:
        options = [p for p in spec["anyOf"] if p.get("type") != "null"]
        return _arg_type(options[0], defs) if options else "null"
    if "enum" in spec:
        return "one of " + "|".join(str(v) for v in spec["enum"])
    if spec.get("format") == "date":
        return "date YYYY-MM-DD"
    if spec.get("type") == "integer":
        low, high = spec.get("minimum"), spec.get("maximum")
        return f"integer {low}-{high}" if low is not None and high is not None else "integer"
    return "text"


def describe_arguments(schema: dict[str, Any]) -> str:
    """Names, required/optional and types only — validation patterns stay in the gateway."""
    defs = schema.get("$defs", {})
    required = set(schema.get("required", []))
    parts = [
        f"{name} ({'required' if name in required else 'optional'}, {_arg_type(spec, defs)})"
        for name, spec in schema.get("properties", {}).items()
    ]
    return "; ".join(parts) or "none"


def build_user_prompt(question: str, tools: list[ToolInfo]) -> str:
    lines = []
    for t in tools:
        kind = "read-only" if t.read_only else "creates a request for human approval"
        description = t.description.split("] ", 1)[-1]  # drop internal [READ_ONLY | ...] tags
        lines.append(
            f"- {t.name} [{kind}]: {description} Arguments: {describe_arguments(t.input_schema)}"
        )
    # The request is JSON-encoded on the last line so it cannot break the prompt structure.
    return (
        "Tools:\n"
        + "\n".join(lines)
        + "\n\n"
        + REQUEST_MARKER
        + " "
        + json.dumps({"question": question}, ensure_ascii=False)
    )


def extract_question(user_prompt: str) -> str:
    """Inverse of the encoding above (used by the deterministic fake model)."""
    line = user_prompt.rsplit("\n", 1)[-1]
    if not line.startswith(REQUEST_MARKER):
        raise LLMError("prompt is missing the request line")
    payload = json.loads(line[len(REQUEST_MARKER) :])
    return str(payload["question"])


_WORD = re.compile(r"[a-z0-9_]+")
_LEAK_WINDOW = 6


def _shingles(text: str) -> set[tuple[str, ...]]:
    words = _WORD.findall(text.lower())
    return {tuple(words[i : i + _LEAK_WINDOW]) for i in range(len(words) - _LEAK_WINDOW + 1)}


def leaks_prompt(text: str, prompt_text: str) -> bool:
    """True if `text` repeats any 6-word run of the prompt (or the request marker)."""
    if REQUEST_MARKER in text:
        return True
    return bool(_shingles(text) & _shingles(prompt_text))


def safe_clarification(text: str | None, prompt_text: str) -> str:
    """Model-written text shown to staff: one plain line that does not echo the prompt."""
    if (
        not text
        or any(ch in text for ch in "{}<>\n\r`")
        or "http" in text.lower()
        or leaks_prompt(text, prompt_text)
    ):
        return GENERIC_CLARIFICATION
    return text


@dataclass(slots=True)
class RoutingResult:
    decision: RouteDecision
    model: str
    primary_failure: str | None = None  # model_error | invalid_model_output
    fallback_model: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)


class Router:
    def __init__(self, llm: LLMClient, fallback: LLMClient | None = None) -> None:
        self.llm = llm
        self.fallback = fallback
        self._schema = RouteDecision.model_json_schema()

    def _ask(self, llm: LLMClient, user: str) -> RouteDecision | str:
        try:
            raw = llm.complete_json(SYSTEM_PROMPT, user, self._schema)
        except LLMError:
            return "model_error"
        if len(raw) > MAX_RAW_REPLY_CHARS:
            return "invalid_model_output"
        try:
            return RouteDecision.model_validate_json(raw)
        except ValidationError:
            return "invalid_model_output"

    def route(self, question: str, tools: list[ToolInfo]) -> RoutingResult:
        user = build_user_prompt(question, tools)
        outcome = self._ask(self.llm, user)
        result = RoutingResult(
            decision=RouteDecision(route=Route.REFUSAL, reason="model_error"),
            model=self.llm.name,
            usage=dict(getattr(self.llm, "last_usage", None) or {}),
        )
        if isinstance(outcome, RouteDecision):
            result.decision = outcome
        else:
            result.primary_failure = outcome
            result.decision = RouteDecision(route=Route.REFUSAL, reason=outcome)
            if self.fallback is not None:
                backup = self._ask(self.fallback, user)
                result.fallback_model = self.fallback.name
                if isinstance(backup, RouteDecision):
                    result.decision = backup
        if result.decision.route is Route.CLARIFICATION:
            # Compare with the instructions and tool catalog, not the staff member's own words.
            catalog = user.rsplit("\n", 1)[0]
            text = safe_clarification(result.decision.clarification, SYSTEM_PROMPT + catalog)
            if text != result.decision.clarification:
                result.decision = result.decision.model_copy(update={"clarification": text})
        return result

    def decide(self, question: str, tools: list[ToolInfo]) -> RouteDecision:
        return self.route(question, tools).decision
