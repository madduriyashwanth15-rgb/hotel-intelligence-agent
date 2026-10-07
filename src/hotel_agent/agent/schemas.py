"""Structured routing output (ADR-0024).

The model's reply must validate against `RouteDecision`. The schema deliberately has no
fields for authorization, approval, SQL, staff identity, role or hotel: a model cannot
express those, let alone grant them. Unknown fields are rejected.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Annotated, Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

MAX_TOOL_ARGUMENTS = 20
MAX_ARGUMENT_CHARS = 500
ARGUMENT_KEY = re.compile(r"[a-z][a-z0-9_]{0,63}")


class Route(StrEnum):
    KNOWLEDGE = "KNOWLEDGE"
    DATA = "DATA"
    TOOL = "TOOL"
    CLARIFICATION = "CLARIFICATION"
    REFUSAL = "REFUSAL"


class ToolRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z_]{0,63}$")]
    # Passed to the ToolGateway unchanged; the gateway's strict schemas decide validity.
    arguments: dict[str, Any] = Field(default_factory=dict, max_length=MAX_TOOL_ARGUMENTS)

    @field_validator("arguments")
    @classmethod
    def _scalar_arguments(cls, value: dict[str, Any]) -> dict[str, Any]:
        # Every tool argument is a scalar; nested objects or huge strings are malformed.
        for key, item in value.items():
            if not ARGUMENT_KEY.fullmatch(key):
                raise ValueError("malformed argument name")
            if item is not None and not isinstance(item, str | int | float | bool):
                raise ValueError("argument values must be scalars")
            if isinstance(item, str) and len(item) > MAX_ARGUMENT_CHARS:
                raise ValueError("argument value too long")
        return value


class RouteDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    route: Route
    reason: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,47}$")]
    tool: ToolRequest | None = None
    clarification: Annotated[str, StringConstraints(max_length=300)] | None = None
    confidence: Annotated[float, Field(ge=0.0, le=1.0)] = 1.0

    @model_validator(mode="after")
    def _consistent(self) -> RouteDecision:
        if (self.route is Route.TOOL) != (self.tool is not None):
            raise ValueError("a tool is required for TOOL and forbidden otherwise")
        if self.route is Route.CLARIFICATION and not self.clarification:
            raise ValueError("CLARIFICATION needs a clarification question")
        return self
