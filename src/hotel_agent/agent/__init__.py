"""Agent orchestration (Phase 5, ADR-0022..0024).

Importing this package forces LangSmith tracing off: `langsmith` is installed only as a
transitive dependency of langchain-core/LangGraph and must never send data (₹0 budget,
owner decision 2026-10-07). API keys are removed from this process's environment.
"""

from __future__ import annotations

import os

_TRACING_FLAGS = ("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2", "LANGCHAIN_TRACING")
_SECRETS = ("LANGSMITH_API_KEY", "LANGCHAIN_API_KEY", "LANGSMITH_ENDPOINT", "LANGCHAIN_ENDPOINT")


def disable_langsmith() -> None:
    for flag in _TRACING_FLAGS:
        os.environ[flag] = "false"
    for name in _SECRETS:
        os.environ.pop(name, None)


disable_langsmith()
