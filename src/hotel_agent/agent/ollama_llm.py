"""Local Ollama model behind `LLMClient.complete_json` (ADR-0025).

Standard library only (no vendor SDK). The client refuses any non-loopback address, so
prompts (synthetic data only) can never leave the machine through this path, and it
bypasses system HTTP proxies for the same reason. Structured output uses Ollama's
`format` = JSON schema; the Router still validates every reply with `RouteDecision`.
"""

from __future__ import annotations

import json
import time
import urllib.request
from typing import Any
from urllib.parse import urlsplit

from hotel_agent.agent.llm import LLMError

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
MAX_RESPONSE_BYTES = 1_000_000


class OllamaLLMClient:
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:11434",
        model: str = "qwen2.5:0.5b",
        *,
        timeout_s: float = 60.0,
        num_ctx: int = 4096,
        num_predict: int = 256,
        keep_alive: str = "30m",
        seed: int = 7,
    ) -> None:
        parts = urlsplit(base_url)
        if parts.scheme != "http" or parts.hostname not in LOOPBACK_HOSTS:
            raise ValueError("Ollama must be reached on a loopback http address (local only)")
        self._url = f"{base_url.rstrip('/')}/api/chat"
        self.model = model
        self._timeout = timeout_s
        self._options = {
            "temperature": 0,
            "seed": seed,
            "num_ctx": num_ctx,
            "num_predict": num_predict,
        }
        self._keep_alive = keep_alive
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.last_usage: dict[str, Any] = {}
        self.totals = {"calls": 0, "errors": 0, "prompt_tokens": 0, "completion_tokens": 0}

    @property
    def name(self) -> str:
        return f"ollama:{self.model}"

    def complete_json(self, system: str, user: str, json_schema: dict[str, Any]) -> str:
        body = {
            "model": self.model,
            "stream": False,
            "format": json_schema,
            "keep_alive": self._keep_alive,
            "options": self._options,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        # The URL scheme and host were checked in __init__ (loopback http only).
        request = urllib.request.Request(  # noqa: S310
            self._url,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        self.totals["calls"] += 1
        self.last_usage = {}
        start = time.perf_counter()
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise LLMError("model response too large")
            payload = json.loads(raw)
            if not payload.get("done"):
                raise LLMError("model response incomplete")
            if payload.get("done_reason") == "length":
                raise LLMError("model output truncated")
            content = payload["message"]["content"]
            if not isinstance(content, str):
                raise LLMError("model content is not text")
        except LLMError:
            self.totals["errors"] += 1
            raise
        except (OSError, ValueError, KeyError, TypeError) as exc:  # URLError/timeout are OSError
            self.totals["errors"] += 1
            raise LLMError(f"ollama request failed: {type(exc).__name__}") from exc
        prompt_tokens = int(payload.get("prompt_eval_count") or 0)
        completion_tokens = int(payload.get("eval_count") or 0)
        self.totals["prompt_tokens"] += prompt_tokens
        self.totals["completion_tokens"] += completion_tokens
        self.last_usage = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "model_ms": round((time.perf_counter() - start) * 1000, 1),
            "load_ms": round(int(payload.get("load_duration") or 0) / 1e6, 1),
        }
        return content
