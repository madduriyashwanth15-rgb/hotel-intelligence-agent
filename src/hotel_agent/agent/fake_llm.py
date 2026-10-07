"""DeterministicRouterLLM: a rule-based stand-in for an LLM router (ADR-0023).

It implements `LLMClient.complete_json` and returns a JSON `RouteDecision`, so the rest
of the graph is exercised exactly as with a real model. It makes routing decisions only;
authorization is never its concern (the ToolGateway and approval workflow decide).
"""

from __future__ import annotations

import json
import re
from datetime import date
from typing import Any

from hotel_agent.agent.router import extract_question
from hotel_agent.sql.context import SchemaContext
from hotel_agent.sql.generators import Generated, Refusal, RefusalCode, RuleBasedFakeGenerator

CODE = re.compile(r"\b([A-Za-z]{2,5}-[A-Za-z0-9]{6})\b")
APPROVAL_ID = re.compile(r"\b([a-f0-9]{24})\b")
ROOM = re.compile(r"\b(?:room|villa)\s+(?:no\.?\s*|number\s*)?(V\d{2}|[1-9]\d{2})\b", re.I)
LAST_NAME = re.compile(
    r"(?:last name|surname|family name)\s*(?:is|=|:)?\s*([A-Za-z][A-Za-z'\-]{1,40})"
    r"|\b(?:guest|mr\.?|ms\.?|mrs\.?|dr\.?)\s+([A-Z][a-zA-Z'\-]{1,40})\b"
)

PII = re.compile(
    r"\b(email|e-mail|phone|mobile|address|passport|contact details|credit card|card number|"
    r"who is staying|who's staying|guest list|home town|date of birth)\b"
)
OUT_OF_SCOPE = re.compile(
    r"\b(weather|stock|share price|joke|poem|song|write (me )?(a |some )?(code|essay|email)|"
    r"recipe|football|cricket score|politic|translate|bitcoin|crypto|homework|"
    r"book (me )?a flight)\b"
)
SQL_LIKE = re.compile(
    r"(\bselect\b.+\bfrom\b|\bdrop\s+table\b|\bdelete\s+from\b|\binsert\s+into\b|\bunion\s+select\b|"
    r"\bupdate\b.+\bset\b|;\s*--|/\*|\bpg_\w+|\bexecute_sql\b|\braw sql\b)"
)
APPROVE = re.compile(
    r"(?:^|[.!:;]\s*|\bplease\s+|\bcan you\s+|\bgo ahead and\s+|\band\s+|\bnow\s+)"
    r"(approve|authori[sz]e|sign off|green[- ]?light|execute|run)\b.{0,40}"
    r"\b(request|approval|refund|task|action|it|this|that|pending)\b"
    r"|\bapprove_request\b|\bexecute_approval\b"
)
OTHER_HOTEL = re.compile(
    r"\b(other hotels?|all hotels|every hotel|hotel tst|test coast|another property)\b"
)
UNSUPPORTED_ACTION = re.compile(
    r"^(please\s+|can you\s+|could you\s+|go ahead and\s+|i need you to\s+)?"
    r"(delete|remove|drop|update|change|modify|set|mark|assign|check (?:in|out)|upgrade|move|"
    r"void|waive|edit|rename|close|reopen|increase|decrease)\b"
)
VAGUE = re.compile(
    r"^(how are we doing|how is business|show me (the )?(numbers|data|everything)|help|"
    r"give me (a|the) report|report|status|summary|dashboard|anything new)\W*$"
)
CREATE_TASK = re.compile(
    r"\b(create|schedule|request|add|log|raise|arrange|send|book|need)\b.{0,60}"
    r"\b(clean(ing)?|turndown|turn-down|deep[- ]clean|inspection|inspect|maintenance|repair|fix)\b"
)
REFUND_ACTION = re.compile(
    r"\b(cancel|process|issue|initiate|make|submit)\b.{0,40}\brefund|\bcancel\b"
    r"|request_cancellation_refund"
)
REFUND_QUOTE = re.compile(
    r"\b(how much|quote|calculate|estimate|would .{0,30}refund|eligible|refund amount|"
    r"what refund)\b"
    r"|calculate_refund"
)
TASK_LIST = re.compile(r"\b(tasks?|housekeeping|to-?do)\b")
KNOWLEDGE_CUES = re.compile(
    r"\b(policy|policies|procedure|sop|rules?|allowed|can a guest|can guests|can i|should|"
    r"what do (i|we)|how do (i|we)|what is the .{0,30}(procedure|policy|rule)|menu|"
    r"opening hours|where is|what time)\b"
)
DATA_CUES = re.compile(
    r"\b(how many|occupancy|revenue|adr|revpar|average booking|booking volume|no-shows?|"
    r"cancellations|arrivals|departures|by channel|length of stay)\b"
)
MIXED_JOINERS = re.compile(
    r"\b(and also|and then|also|plus|as well as)\b|;| and (?:what|how|create|schedule|look up)\b"
)


def _decision(route: str, reason: str, **kw: Any) -> str:
    return json.dumps({"route": route, "reason": reason, **kw})


def _clarify(reason: str, question: str) -> str:
    return _decision("CLARIFICATION", reason, clarification=question)


def _task_type(q: str) -> str:
    if re.search(r"deep[- ]clean", q):
        return "deep_clean"
    if re.search(r"turn-?down", q):
        return "turndown"
    if re.search(r"inspect", q):
        return "inspection"
    if re.search(r"maintenance|repair|fix", q):
        return "maintenance"
    return "cleaning"


def _priority(q: str) -> str:
    if re.search(r"\b(urgent|asap|immediately|emergency)\b", q):
        return "urgent"
    if re.search(r"\bhigh priority|\bhigh\b", q):
        return "high"
    if re.search(r"\blow priority|\blow\b", q):
        return "low"
    return "normal"


def _reason(q: str) -> str:
    if "medical" in q or "hospital" in q:
        return "medical"
    if re.search(r"flight|travel|road|landslide|disruption", q):
        return "travel_disruption"
    if "duplicate" in q:
        return "duplicate_booking"
    return "guest_request"


class DeterministicRouterLLM:
    """Rule-based router model. Same interface as a real JSON-mode LLM client."""

    def __init__(self, business_date: date = date(2026, 10, 1)) -> None:
        self._sql_rules = RuleBasedFakeGenerator()
        self._ctx = SchemaContext(business_date=business_date)

    @property
    def name(self) -> str:
        return "deterministic-router-v1"

    def complete_json(self, system: str, user: str, json_schema: dict[str, Any]) -> str:
        return self.route(extract_question(user))

    def _data_signal(self, question: str) -> bool:
        result = self._sql_rules.generate(question, self._ctx)
        if not DATA_CUES.search(question.lower()):
            return False
        return isinstance(result, Generated) or (
            isinstance(result, Refusal) and result.code is RefusalCode.AMBIGUOUS_QUESTION
        )

    def route(self, question: str) -> str:
        q = " ".join(question.lower().split())
        code = CODE.search(question)
        approval = APPROVAL_ID.search(question)
        room = ROOM.search(question)

        if SQL_LIKE.search(q):
            return _decision("REFUSAL", "sql_content_not_accepted")
        if PII.search(q):
            return _decision("REFUSAL", "personal_data_request")
        if OUT_OF_SCOPE.search(q):
            return _decision("REFUSAL", "out_of_scope")
        if APPROVE.search(q):
            return _decision("REFUSAL", "approval_not_allowed")
        if OTHER_HOTEL.search(q):
            return _decision("REFUSAL", "other_hotel_not_allowed")
        if VAGUE.search(q):
            return _clarify(
                "too_vague",
                "What exactly do you need: a policy answer, a figure, or a specific booking,"
                " room or task?",
            )

        tool_signal = bool(code or approval or room or CREATE_TASK.search(q))
        knowledge_signal = bool(KNOWLEDGE_CUES.search(q))
        data_signal = self._data_signal(question)
        if MIXED_JOINERS.search(q) and sum((tool_signal, knowledge_signal, data_signal)) >= 2:
            return _clarify(
                "mixed_request",
                "Please ask one thing at a time: a policy question, a figure, or one"
                " booking/room/task.",
            )

        if (
            UNSUPPORTED_ACTION.search(q)
            and not CREATE_TASK.search(q)
            and not REFUND_ACTION.search(q)
        ):
            return _decision("REFUSAL", "unsupported_action")
        if approval:
            return _decision(
                "TOOL",
                "approval_status",
                tool={
                    "name": "get_approval_status",
                    "arguments": {"approval_id": approval.group(1)},
                },
            )
        if code:
            name_match = LAST_NAME.search(question)
            if name_match is None:
                return _clarify(
                    "missing_last_name",
                    "Please give the guest's last name together with the confirmation code.",
                )
            args = {
                "confirmation_code": code.group(1).upper(),
                "guest_last_name": name_match.group(1) or name_match.group(2),
            }
            if REFUND_ACTION.search(q) and not REFUND_QUOTE.search(q):
                return _decision(
                    "TOOL",
                    "refund_request",
                    tool={
                        "name": "request_cancellation_refund",
                        "arguments": {**args, "reason": _reason(q)},
                    },
                )
            if "refund" in q:
                return _decision(
                    "TOOL", "refund_quote", tool={"name": "calculate_refund", "arguments": args}
                )
            return _decision(
                "TOOL", "reservation_lookup", tool={"name": "get_reservation", "arguments": args}
            )
        if CREATE_TASK.search(q):
            if not room:
                return _clarify("missing_room", "Which room is the task for?")
            return _decision(
                "TOOL",
                "housekeeping_request",
                tool={
                    "name": "create_housekeeping_task",
                    "arguments": {
                        "room_number": room.group(1).upper(),
                        "task_type": _task_type(q),
                        "priority": _priority(q),
                    },
                },
            )
        if UNSUPPORTED_ACTION.search(q):
            return _decision("REFUSAL", "unsupported_action")
        if room:
            if TASK_LIST.search(q):
                return _decision(
                    "TOOL",
                    "task_lookup",
                    tool={
                        "name": "get_housekeeping_tasks",
                        "arguments": {"room_number": room.group(1).upper()},
                    },
                )
            return _decision(
                "TOOL",
                "room_lookup",
                tool={
                    "name": "get_room_status",
                    "arguments": {"room_number": room.group(1).upper()},
                },
            )
        if re.search(r"\b(list|show)\b.{0,30}\b(open|pending)\b.{0,20}\btasks?\b", q):
            return _decision(
                "TOOL",
                "task_lookup",
                tool={"name": "get_housekeeping_tasks", "arguments": {"status": "open"}},
            )
        if data_signal and not knowledge_signal:
            return _decision("DATA", "operational_figure")
        return _decision("KNOWLEDGE", "hotel_knowledge")
