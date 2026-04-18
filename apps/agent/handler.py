"""Voice agent handler — Stories 1.2 + 1.3 + 1.4 + 2.1 + 2.2.

Story 2.1 adds spot-specific reasoning on top of the Story 1.4 booking
loop: the model may now call ``discover_spots`` to fetch a classroom
layout, pick a concrete ``spot_id``, and either book it directly or
recover gracefully via a neighbor spot when the first choice is taken
(pre-flight: layout says is_available=false; post-flight: MT returned
``spot_unavailable``). The recovery refresh runs handler-side (no
extra LLM turn) and never hangs up (NFR3).

Story 2.2 adds the modify-reservation flow (cancel / reschedule) on
top of that. Two new tools (``list_user_reservations`` +
``cancel_reservation``) ride the existing tool loop; the handler
stashes the narrated reservations so the cancel confirmation can
echo a friendly ``class_name`` / ``start_time_local`` and tracks a
``_pending_reschedule_source`` scratch so the create-reservation
leg can distinguish ``last_action=rescheduled`` from the pure-book
``last_action=booked``. Orphaned-cancel narration (cancel landed,
create failed) is surfaced via ``_orphaned_cancel`` on Context so
the streaming turn never silently leaves a user stranded.

See ``_bmad-output/implementation-artifacts/2-1-spot-specific-
reasoning.md`` for AC mapping. Stories 1.2 / 1.3 / 1.4 technical
behavior (``voice="kore"``, 3/6/9 timeout policy, session-state
contract, two-turn tool loop, streaming narration) is preserved
verbatim — Story 2.1 only extends ``SYSTEM_PROMPT`` and
``_dispatch_tool_calls``. Persona copy (GREETING, GOODBYE, timeout
lines, SYSTEM_PROMPT) was later rebranded from Kai/SoHo to
Devin/Barry's Chelsea with an AI-concierge self-disclosure; the tool
loop and contract keys are untouched.

Design notes (worth reading before editing):

* **Two-turn tool pattern (AC11)**. First turn is NON-streaming so we
  can inspect ``response.message.tool_calls``. If tools fire, we
  dispatch via ``tools.TOOL_DISPATCH``, append ``ToolResultMessage``
  frames (pass the executor's dict directly — the primfunctions type
  wants ``dict[str, Any]``, not a JSON string), then run a SECOND
  streaming completion to narrate. Streaming on the second turn
  preserves the Story 1.2 NFR1 win (first sentence speaks as soon as
  it lands).
* **Code-side gate guard (AC3)**. The system prompt tells the model
  "call ``check_payment_options`` first". If the model nevertheless
  emits ``list_class_sessions`` without a prior successful
  ``check_payment_options`` (either in this tool-call batch or from a
  cached ``context.get_data("eligible")``), we inject an error
  ``ToolResultMessage`` and let the model retry on the second turn.
* **``chosen_class`` resolver (AC7)**. We stash the narrated
  ``_last_session_options`` list on Context so that a follow-up
  utterance ("the 6", "yeah, the second one") can be matched back to
  a concrete session id without re-fetching. Resolver is deliberately
  simple — exact substring + ordinal match — Story 2.1 owns
  sophisticated spot / option reasoning.
* **``booking_progress`` DebugEvent (AC8)**. Emitted exactly once per
  ``TextEvent`` after TTS has yielded. Fields follow ``docs/schema.md``
  and only include populated session-state keys. ``MT_ACCESS_TOKEN``
  is never serialized into the event.
* **No new deps (AC10)**. ``requirements.txt`` stays at one line.
  MT HTTP lives in ``tools.py`` behind ``asyncio.to_thread(urllib…)``.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from primfunctions.events import (
    DebugEvent,
    Event,
    StartEvent,
    StopEvent,
    TextEvent,
    TextToSpeechEvent,
    TimeoutEvent,
)

# NOTE (carried from Story 1.2): ``primfunctions.actions`` does not exist in
# runtime 1.x. ``StopEvent`` from ``primfunctions.events`` doubles as the
# hangup action; the alias below keeps the skill-doc readability.
StopAction = StopEvent

from primfunctions.context import Context
from primfunctions.completions import (
    SystemMessage,
    ToolResultMessage,
    UserMessage,
    configure_provider,
    deserialize_conversation,
    generate_chat_completion,
    generate_chat_completion_stream,
)

from config import CONFIG, bootstrap_config
from tools import MAX_NARRATED_SESSIONS, TOOL_DISPATCH, TOOLS

TIMEOUT_MAX_COUNT = 9
AGENT_VOICE = "kore"

LLM_PROVIDER = "google"
LLM_MODEL = "gemini-2.5-flash"

GREETING = (
    "Barry's Chelsea — Devin here, your AI concierge. I can book, "
    "modify, or answer burning questions. What do you need?"
)
GOODBYE = "Thanks for calling Barry's Chelsea — see you in the Red Room!"
TIMEOUT_NUDGE = "Still with me?"
TIMEOUT_HANGUP = (
    "Looks like we lost you — give us a call back anytime. "
    "See you in the Red Room!"
)
# Emitted when a TextEvent turn completes without ANY
# TextToSpeechEvent — last-line defense against dead air (the
# 16-second silence that fired TIMEOUT_NUDGE on session
# 08096e86-c177-453f-85b2-573f03acad63). Happens when the streaming
# loop exits with zero text chunks (model chose to emit nothing, or
# MAX_STREAM_ROUNDS hit while still tool-calling). Generic + friendly
# on purpose — we don't know WHY the model went silent, so apologizing
# or claiming misunderstanding would be wrong more often than not.
SILENCE_FALLBACK = "Got that — anything else?"

# Canonical closer spoken deterministically after every successful
# create_reservation — bypassing the streaming narration entirely.
# Regression driver: debugger-session-2026-04-18T14-12-23 showed
# Gemini paraphrasing the "say exactly: X" prompt instruction into
# "Great—you're booked for the Sunday 12:00 PM Boot Camp…", which
# violates brand copy and leaks internal class metadata. Prompt-only
# fixes are unreliable on Gemini when the model also wants to echo
# details. The handler now yields this string directly on the
# booking turn and skips the LLM narration round altogether — zero
# paraphrase risk, one fewer LLM round-trip on the happy path.
BOOKING_CONFIRMATION = "You're locked in. Anything else before you go?"

# System prompt kept under ~170 words for NFR1. The payment-gate,
# confirm-to-book, and named-spot rules are deliberately terse — long
# instructions regress first-sentence latency. Persona was rebranded
# from Kai/SoHo to Devin/Barry's Chelsea; tone targets are "coach, not
# robot" — high-performance, slightly playful, short and punchy. The
# two exact closers ("You're locked in…" / "…Red Room?") are quoted
# verbatim so the model doesn't paraphrase the brand lines. The
# confirm-to-book rule explicitly rejects exploratory questions
# ("can we do X?", "what about X?") as confirmations — live-call
# regression showed Gemini would otherwise book on inquiries and
# auto-assign a spot before the caller named one.
SYSTEM_PROMPT = (
    "You are Devin, front-desk concierge at Barry's Chelsea. Voice "
    "only — reply in ONE short sentence, coach-like and confident, "
    "playful but efficient, no lists, no markdown. "
    "BOOKING FLOW: on a booking intent, call check_payment_options "
    "first. Only call list_class_sessions if eligible=true; if "
    "eligible=false, apologize briefly and offer a callback. Narrate "
    "at most three options using the start_time_local strings "
    "verbatim, ending with an implicit-confirmation prompt (e.g. "
    "'Want the 6?') — never ask 'would you like me to list classes'. "
    "Book ONLY on firm confirmation — the caller says 'yes', 'book "
    "it', 'sounds good', or names a spot. Treat 'can we do X?', "
    "'what about X?', 'how about X?' as exploration — re-narrate "
    "and wait, do NOT call create_reservation. On a firm booking, "
    "call create_reservation with that session's id, then say "
    "exactly: \"You're locked in. Anything else before you go?\" "
    "For a named spot, call discover_spots first, then use the "
    "matched id; if taken, offer one neighbor from neighbor_ids. "
    "MODIFY FLOW: on cancel/reschedule intent, call "
    "list_user_reservations first; if the pick is ambiguous, "
    "re-ask before any mutation. On firm confirmation you MUST "
    "call cancel_reservation with that reservation_id — never "
    "say 'Done' without the tool result. For a reschedule, after "
    "cancel succeeds call list_class_sessions (only if you don't "
    "already know the target session id) then create_reservation "
    "directly — SKIP check_payment_options on the reschedule's "
    "book leg, credits were just freed. Then say exactly 'Done "
    "— your {time} is canceled.' or 'Moved you to the {new_time} "
    "— you're locked in.' If either leg fails, narrate what "
    "actually landed in one sentence — no silent orphans. "
    "FAQ: for hours/pricing/amenities/location/class-type "
    "questions, call lookup_studio_info(topic) and speak the "
    "answer in ONE sentence; on empty or unknown_topic, say "
    "\"I don't have that off-hand — want me to get you into a "
    "class instead?\" — never invent. "
    "After answering a non-booking question, close with: \"Anything "
    "else before you hit the Red Room?\" "
    "END CALL: when the caller says they're done — 'no thanks', "
    "'that's all', 'I'm good', 'bye', 'we're done' — call end_call "
    "with a short on-brand closing_speech like \"Thanks for calling "
    "Barry's Chelsea — see you in the Red Room!\". Do NOT call "
    "end_call mid-flow or if the caller asked another question."
)

# Keys that may land in booking_progress.event_data per docs/schema.md.
# Only populated keys are forwarded; token and scratch keys are filtered.
_BOOKING_PROGRESS_KEYS = (
    "name",
    "date",
    "time",
    "phone",
    "chosen_class",
    "chosen_spot",
    "guest_name",
    "last_action",
)

# Story 2.2 modify-intent detector. Matched case-insensitively as
# substrings against the raw caller utterance. ``reschedul`` (no
# trailing ``e``) catches both ``reschedule`` and ``rescheduling``.
_MODIFY_INTENT_TOKENS = (
    "cancel",
    "reschedul",
    "move my",
    "switch my",
    "change my",
)

# Story 2.7 FAQ-intent detector (AC5). Case-insensitive substring
# match against the raw caller utterance. ``amenit`` catches both
# ``amenity`` and ``amenities``; ``pric`` would over-match ``price``
# so we list ``price`` and ``pricing`` explicitly. ``close`` covers
# "when do you close" and "are you close by"; deliberately kept even
# though it can false-positive on phrases like "close my account" —
# the modify-intent tokens do NOT list "close", so the ordering in
# the TextEvent branch (FAQ first, modify second) keeps cancellation
# utterances from landing here ("cancel" wins before "close" is
# checked, because "cancel" appears in _MODIFY_INTENT_TOKENS).
# Actually: we check FAQ FIRST so mid-booking detours (AC8) work;
# utterances like "cancel my 6" hit _looks_like_modify on the ``cancel``
# token while never matching any FAQ token, so precedence is safe.
_FAQ_INTENT_TOKENS = (
    "hours",
    "open",
    "close",
    "cost",
    "price",
    "pricing",
    "shower",
    "locker",
    "amenit",
    "where are you",
    "address",
    "located",
    "what kind",
    "class types",
    "signature",
)

def _looks_like_book_class(text: str) -> bool:
    lowered = text.lower()
    return "book" in lowered and "class" in lowered


def _looks_like_modify(text: str) -> bool:
    lowered = text.lower()
    return any(tok in lowered for tok in _MODIFY_INTENT_TOKENS)


def _looks_like_faq(text: str) -> bool:
    """Story 2.7 AC5 — FAQ detour detector.

    Returns True if the caller's utterance contains any ``_FAQ_INTENT_TOKENS``
    substring (case-insensitive). Used to flip ``intent`` to
    ``"studio_info"`` for the turn; booking scratch state (``chosen_class``,
    ``chosen_spot``, ``_last_session_options``, ``_last_listed_reservations``,
    etc.) is deliberately NOT cleared — a mid-booking FAQ question is a
    feature, and the booking flow picks up on the caller's next utterance.
    """
    lowered = text.lower()
    return any(tok in lowered for tok in _FAQ_INTENT_TOKENS)


def _ensure_completions_provider(context: Context) -> None:
    if context.get_data("_completions_ready", False):
        return
    configure_provider(LLM_PROVIDER, voicerun_managed=True)
    context.set_data("_completions_ready", True)


def _ensure_system_prompt(context: Context) -> None:
    if not context.get_completion_messages():
        context.set_completion_messages([SystemMessage(content=SYSTEM_PROMPT)])


def _ensure_session_state(context: Context) -> None:
    """Session-state contract. Story 1.3 adds ``eligible`` and the scratch
    ``_last_session_options`` / ``_turn`` keys on top of Story 1.2's five.
    """
    if context.get_data("intent", None) is None:
        context.set_data("intent", None)
    if context.get_data("slots", None) is None:
        context.set_data("slots", {})
    for key in ("chosen_class", "chosen_spot", "guest_name", "last_action"):
        if context.get_data(key, None) is None:
            context.set_data(key, None)


def _bump_turn(context: Context) -> int:
    turn = int(context.get_data("_turn", 0) or 0) + 1
    context.set_data("_turn", turn)
    return turn


def _resolve_chosen_class(context: Context, user_text: str) -> None:
    """Naïve confirmation resolver for AC7.

    Story 2.1 will replace this with spot-level reasoning. We only need
    to catch "yeah the 6", "the 6 PM one", "second one", etc. against
    the options narrated on the previous turn.
    """
    options = context.get_data("_last_session_options", None) or []
    if not options:
        return

    lowered = user_text.lower()

    # Ordinal match first — "first" / "second" / "third".
    ordinals = {"first": 0, "1st": 0, "second": 1, "2nd": 1, "third": 2, "3rd": 2}
    for phrase, idx in ordinals.items():
        if phrase in lowered and idx < len(options):
            _commit_chosen_class(context, options[idx])
            return

    # Time-token match — "the 6", "6 PM", "at 6:00".
    tokens = re.findall(r"\b\d{1,2}(?::\d{2})?\s*(?:am|pm)?\b", lowered)
    if not tokens:
        return
    for option in options:
        label = (option.get("start_time_local") or "").lower()
        if not label:
            continue
        for tok in tokens:
            normalized = tok.replace(" ", "")
            if normalized and normalized in label.replace(" ", ""):
                _commit_chosen_class(context, option)
                return


def _commit_chosen_class(context: Context, option: dict) -> None:
    context.set_data(
        "chosen_class",
        {
            "id": option.get("id"),
            "name": option.get("name"),
            "start_time_local": option.get("start_time_local"),
        },
    )
    # Scratch cleanup — once a caller confirms, we no longer need the
    # narrated list as a disambiguation target.
    context.set_data("_last_session_options", None)


def _collect_booking_progress(context: Context) -> dict:
    data: dict = {}
    for key in _BOOKING_PROGRESS_KEYS:
        val = context.get_data(key, None)
        if val is None:
            continue
        if key == "chosen_class" and isinstance(val, dict):
            # Schema expects ``chosen_class`` as a string for the
            # dashboard renderer; keep name+time so it's readable.
            label = val.get("name") or ""
            when = val.get("start_time_local") or ""
            data["chosen_class"] = f"{label} @ {when}".strip(" @")
            continue
        data[key] = val
    return data


def _booking_progress_event(context: Context, turn_id: int) -> DebugEvent:
    event_data = _collect_booking_progress(context)
    ctx_payload = {
        "turn_id": str(turn_id),
        "session_id": getattr(context, "session_id", "") or "",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    # AC8 redaction invariant. Cheap assert — the token is a short opaque
    # string; substring check is sufficient and avoids a full walk. Guard
    # against ``""`` (pre-bootstrap) which trivially "contains" anything.
    if CONFIG.mt_access_token:
        serialized = json.dumps({"event_data": event_data, "context": ctx_payload})
        assert CONFIG.mt_access_token not in serialized, (
            "MT_ACCESS_TOKEN leaked into booking_progress payload"
        )
    return DebugEvent(
        event_name="booking_progress",
        event_data=event_data,
        direction="out",
        context=ctx_payload,
    )


def _find_last_discover_spots(messages: list) -> list:
    """Walk ``messages`` backwards, return the ``spots`` list from the
    most recent successful ``discover_spots`` ToolResultMessage (or
    ``[]`` if none has fired this conversation).

    Used by Story 2.1 Tasks 2.2 / 2.5 / 2.6 — reverse-lookup a
    caller-facing ``label`` + ``neighbor_ids`` from an MT ``spot_id``
    the model just emitted in a ``create_reservation`` call. The
    conversation list is short (a few turns worst-case) so a backward
    linear scan is cheap; no need for a dedicated cache on Context.
    """
    for msg in reversed(messages):
        if not isinstance(msg, ToolResultMessage):
            continue
        if getattr(msg, "name", None) != "discover_spots":
            continue
        content = getattr(msg, "content", None) or {}
        if not isinstance(content, dict):
            continue
        spots = content.get("spots") or []
        if isinstance(spots, list):
            return spots
    return []


def _match_spot_by_id(spots: list, spot_id: str) -> dict:
    """Return the spot dict whose ``id`` equals ``spot_id`` (stringified
    compare), or ``{}`` if no match. Case-sensitive; MT ids are opaque
    integers as strings.
    """
    target = str(spot_id or "").strip()
    if not target:
        return {}
    for spot in spots:
        if not isinstance(spot, dict):
            continue
        if str(spot.get("id") or "") == target:
            return spot
    return {}


def _match_spot_by_label(spots: list, label: str) -> dict:
    """Return the spot dict whose ``label`` equals ``label`` (case-
    insensitive exact match first, then lowercase substring fallback),
    or ``{}`` if no match. Used by the post-flight recovery path to
    re-resolve ``neighbor_ids`` on a refreshed classroom layout.
    """
    needle = (label or "").strip().lower()
    if not needle:
        return {}
    for spot in spots:
        if not isinstance(spot, dict):
            continue
        if str(spot.get("label") or "").strip().lower() == needle:
            return spot
    # Substring fallback — MT may have renamed the spot slightly.
    for spot in spots:
        if not isinstance(spot, dict):
            continue
        if needle in str(spot.get("label") or "").strip().lower():
            return spot
    return {}


def _utterance_mentions_label(user_text: str, label: str) -> bool:
    """Naïve matcher for AC3 / Task 2.6 pre-flight detection.

    Returns True if ``label.lower()`` is a substring of
    ``user_text.lower()``, OR if the trailing number of the label
    (e.g. "Bike 5" → "5") appears as a standalone digit token in the
    utterance. Kept intentionally simple — Story 2.1 owns basic spot
    matching; richer fuzzy matching is a future hardening task.
    """
    if not user_text or not label:
        return False
    u_lower = user_text.lower()
    l_lower = label.strip().lower()
    if l_lower and l_lower in u_lower:
        return True
    # Trailing-number fallback ("bike 5" → "5").
    m = re.search(r"(\d+)\s*$", l_lower)
    if not m:
        return False
    num = m.group(1)
    # Only match if the utterance contains the same digit token — not
    # embedded in another word (e.g. "5" matches "bike 5" but not
    # "50 reps"). Use a word-ish boundary.
    return bool(re.search(rf"(?<!\d){re.escape(num)}(?!\d)", u_lower))


async def _dispatch_tool_calls(
    context: Context,
    messages: list,
    tool_calls: list,
    payment_checked_this_turn: bool,
) -> bool:
    """Dispatch each ``tool_call``, append ``ToolResultMessage`` frames,
    and apply name-specific side effects:

    * ``check_payment_options`` — eligibility cache on Context.
    * ``list_class_sessions`` — narrated-options stash for the
      ``_resolve_chosen_class`` ordinal / time-token resolver.
    * ``discover_spots`` (Story 2.1) — on pre-flight miss (caller
      named a taken spot), stash ``_last_requested_spot`` +
      preload ``chosen_spot`` for this turn's ``booking_progress``
      DebugEvent.
    * ``create_reservation`` —
      * BEFORE dispatch: on non-empty ``spot_id``, reverse-lookup
        ``label`` / ``neighbor_ids`` from the most recent
        ``discover_spots`` result and stash ``_last_requested_spot``
        so the post-flight recovery path has the caller-friendly
        label even after the tool result is appended.
      * AFTER success: merge ``reservation_id`` (and ``spot_id`` /
        ``spot_label`` when present) into ``chosen_class``; promote
        ``_last_requested_spot.label`` to ``chosen_spot`` (or the
        raw id as a fallback so the dashboard never renders ``""``).
      * AFTER ``spot_unavailable`` failure: deterministically refresh
        ``discover_spots`` once for the same class_session, re-resolve
        neighbors, and preload ``chosen_spot`` with the first fresh
        neighbor label so ``booking_progress`` on THIS turn reflects
        the offer. Does NOT append a synthetic ToolResultMessage —
        the model already saw the failure result and will narrate
        the apology on the streaming turn.

    Returns the updated ``payment_checked_this_turn`` flag. Shared
    between the non-streaming first turn and the streaming narration
    turn(s) — Gemini occasionally splits tool calls across turns, so
    the streaming turn must be able to dispatch and re-stream.
    """
    any_stashed_options: list = []

    for call in tool_calls:
        name = call.function.name
        raw_args = call.function.arguments
        # ``FunctionCall.arguments`` is typed as ``dict`` but older server
        # paths can ship a JSON string. Normalize defensively.
        if isinstance(raw_args, str):
            try:
                args = json.loads(raw_args) if raw_args else {}
            except json.JSONDecodeError:
                args = {}
        else:
            args = dict(raw_args or {})

        # ----- Story 2.1 Task 2.2 — BEFORE-dispatch scratch stash.
        # Done before the executor runs so the scratch is available
        # to the post-flight recovery path even if dispatch happens
        # across two streaming rounds. Skipped when spot_id is empty
        # (auto-assign path) — nothing to record.
        if name == "create_reservation":
            spot_arg = str(args.get("spot_id") or "").strip()
            if spot_arg:
                prior_spots = _find_last_discover_spots(messages)
                matched = _match_spot_by_id(prior_spots, spot_arg)
                context.set_data(
                    "_last_requested_spot",
                    {
                        "label": str(matched.get("label") or ""),
                        "id": spot_arg,
                        "neighbor_ids": list(
                            matched.get("neighbor_ids") or []
                        ),
                    },
                )

        if name == "list_class_sessions" and not payment_checked_this_turn:
            # AC3 fallback guard. Short-circuit before hitting MT.
            result: dict = {
                "error": "payment_not_checked",
                "hint": (
                    "Call check_payment_options first to confirm the "
                    "caller can book, then try again."
                ),
            }
        else:
            executor = TOOL_DISPATCH.get(name)
            if executor is None:
                result = {"error": "unknown_tool", "name": name}
            else:
                result = await executor(args)

        if name == "check_payment_options":
            eligible = bool(result.get("eligible"))
            context.set_data("eligible", eligible)
            payment_checked_this_turn = payment_checked_this_turn or eligible

        if name == "list_class_sessions":
            sessions = result.get("sessions") or []
            if sessions:
                any_stashed_options = sessions

        # ----- end_call hangup signal. Promoted to a
        # ``StopAction(closing_speech=..., voice=...)`` yield by the
        # TextEvent branch on the same turn. Stored as the raw
        # closing_speech string (or the canonical ``GOODBYE`` when
        # the model omitted it) so the caller always hears a brand-
        # safe sign-off even if the model flubbed the arg. The
        # ToolResultMessage is still appended below so the
        # conversation transcript reflects the termination decision;
        # Gemini tolerates a hangup tool with a normal success
        # payload.
        if name == "end_call" and result.get("success") is True:
            closing = str(result.get("closing_speech") or "").strip()
            context.set_data(
                "_end_call_requested", closing or GOODBYE
            )

        # ----- Story 2.2 AC11 — stash the narrated reservations so
        # the cancel/reschedule confirmation copy can look up
        # class_name + start_time_local by reservation_id without a
        # second GET. Underscore-prefixed → filtered out of
        # booking_progress via _BOOKING_PROGRESS_KEYS.
        if name == "list_user_reservations":
            reservations = result.get("reservations") or []
            if reservations:
                context.set_data(
                    "_last_listed_reservations", reservations
                )

        # ----- Story 2.1 Task 2.6 — pre-flight miss detection.
        # If the caller's last utterance named a spot that
        # discover_spots just reported is_available=false, stash the
        # scratch + preload chosen_spot so THIS turn's booking_progress
        # DebugEvent reflects the offer (AC10). Skipped silently when
        # discover_spots failed or returned empty.
        if name == "discover_spots":
            spots_list = result.get("spots") or []
            user_text = str(
                context.get_data("_last_user_text") or ""
            )
            if spots_list and user_text:
                taken_match: dict = {}
                for spot in spots_list:
                    if not isinstance(spot, dict):
                        continue
                    if spot.get("is_available"):
                        continue
                    label = str(spot.get("label") or "")
                    if _utterance_mentions_label(user_text, label):
                        taken_match = spot
                        break
                if taken_match:
                    context.set_data(
                        "_last_requested_spot",
                        {
                            "label": str(taken_match.get("label") or ""),
                            "id": str(taken_match.get("id") or ""),
                            "neighbor_ids": list(
                                taken_match.get("neighbor_ids") or []
                            ),
                        },
                    )
                    context.set_data(
                        "chosen_spot", str(taken_match.get("label") or "")
                    )

        if name == "create_reservation" and result.get("success") is True:
            # Story 2.2 — the reschedule path sets
            # _pending_reschedule_source on the prior cancel success;
            # on that leg the spoken closer is "Moved you to the
            # {new_time} — you're locked in." (model-streamed, not
            # the canonical booking closer). Gate the short-circuit
            # flag + last_action verb on this distinction.
            pending_reschedule = context.get_data(
                "_pending_reschedule_source"
            )
            is_reschedule = pending_reschedule is not None
            if is_reschedule:
                context.set_data("last_action", "rescheduled")
            else:
                # Per-turn flag consumed by the TextEvent handler to
                # short-circuit the streaming narration and yield
                # BOOKING_CONFIRMATION verbatim (see constant docstring).
                # Explicitly NOT persisted across turns — the TextEvent
                # branch clears it on entry. Flag is set BEFORE the
                # chosen_class merge so the handler can rely on it even
                # if a later block in this loop raises.
                context.set_data("_booked_this_turn", True)
                # Story 2.2 AC10 — mirror Story 1.4's success verb on
                # the dashboard so the renderer always has a concrete
                # last_action on confirmation turns.
                context.set_data("last_action", "booked")

            # AC7 precedence: the MT POST used args["class_session_id"]
            # verbatim (the executor never second-guesses model args);
            # if the model picked a different id than ``chosen_class``,
            # trust the model — that id is what MT actually booked.
            existing = dict(context.get_data("chosen_class") or {})
            model_sid = str(args.get("class_session_id") or "").strip()
            echo_name = (
                result.get("class_name") or existing.get("name") or ""
            )
            echo_time = (
                result.get("start_time_local")
                or existing.get("start_time_local")
                or ""
            )
            merged = {
                **existing,
                "id": model_sid or existing.get("id"),
                "name": echo_name or existing.get("name"),
                "start_time_local": (
                    echo_time or existing.get("start_time_local")
                ),
                "reservation_id": result["reservation_id"],
            }

            # ----- Story 2.1 Task 2.4 — promote chosen_spot + merge
            # spot_id / spot_label into chosen_class when the booking
            # included a specific spot. Fallback to the raw id string
            # if the label reverse-lookup missed (e.g. model called
            # create_reservation without a prior discover_spots) so
            # the dashboard never renders "".
            spot_arg = str(args.get("spot_id") or "").strip()
            if spot_arg:
                last = context.get_data("_last_requested_spot") or {}
                label = str(last.get("label") or "")
                displayed = label or spot_arg
                merged["spot_id"] = spot_arg
                merged["spot_label"] = label
                context.set_data("chosen_spot", displayed)
                context.set_data("_last_requested_spot", None)

            context.set_data("chosen_class", merged)
            # Backfill echo fields on the LLM-visible result so the
            # narration turn has concrete strings to speak even when
            # the model omitted them (Story 1.4 Task 2.2 / AC3).
            result = {
                **result,
                "class_name": echo_name,
                "start_time_local": echo_time,
            }

        # ----- Story 2.1 Task 2.5 — post-flight recovery.
        # Runs ONLY on create_reservation ``spot_unavailable`` when
        # we have a stashed _last_requested_spot. Refreshes the
        # classroom layout deterministically (handler-side, not via
        # LLM), updates neighbor_ids on the scratch, and preloads
        # chosen_spot with the first fresh neighbor label so THIS
        # turn's booking_progress reflects the offer. Does NOT append
        # a synthetic ToolResultMessage — the model already saw the
        # failure result on ``messages`` and will narrate the apology
        # on the streaming turn (AC7 post-flight).
        if (
            name == "create_reservation"
            and result.get("success") is False
            and result.get("error") == "spot_unavailable"
        ):
            last = dict(context.get_data("_last_requested_spot") or {})
            if last:
                class_session_id = str(
                    args.get("class_session_id") or ""
                ).strip()
                refresh_executor = TOOL_DISPATCH.get("discover_spots")
                if class_session_id and refresh_executor is not None:
                    refresh = await refresh_executor(
                        {"class_session_id": class_session_id}
                    )
                    fresh_spots = (
                        refresh.get("spots") or []
                        if isinstance(refresh, dict)
                        else []
                    )
                    # Try label match first, fall back to id (MT may
                    # have renumbered, unlikely but cheap to handle).
                    fresh_entry = _match_spot_by_label(
                        fresh_spots, last.get("label") or ""
                    ) or _match_spot_by_id(
                        fresh_spots, last.get("id") or ""
                    )
                    refreshed_neighbors = list(
                        fresh_entry.get("neighbor_ids") or []
                    )
                    last["neighbor_ids"] = refreshed_neighbors
                    context.set_data("_last_requested_spot", last)
                    # Preload the first available neighbor's label into
                    # chosen_spot. Fall back to whatever was requested
                    # so booking_progress is never blank on the
                    # recovery turn.
                    offered_label = ""
                    for nid in refreshed_neighbors:
                        neighbor = _match_spot_by_id(fresh_spots, nid)
                        if neighbor.get("is_available"):
                            offered_label = str(
                                neighbor.get("label") or ""
                            )
                            if offered_label:
                                break
                    context.set_data(
                        "chosen_spot",
                        offered_label or str(last.get("label") or ""),
                    )

        # ----- Story 2.2 — cancel_reservation success: stamp the
        # dashboard verb, update chosen_class for the renderer, stash
        # a _pending_reschedule_source marker so the subsequent
        # create_reservation (if any) can distinguish rescheduled
        # from booked, and backfill echo fields on the LLM-visible
        # result so the narration has concrete strings to speak.
        if name == "cancel_reservation" and result.get("success") is True:
            rid = str(result.get("reservation_id") or "").strip()
            echo_name = str(result.get("class_name") or "").strip()
            echo_time = str(result.get("start_time_local") or "").strip()

            # AC4 backfill — if the model omitted the echo fields,
            # look them up from the prior list_user_reservations
            # stash so "Done — your {time} is canceled." reads
            # naturally.
            if not echo_name or not echo_time:
                listed = (
                    context.get_data("_last_listed_reservations") or []
                )
                for entry in listed:
                    if not isinstance(entry, dict):
                        continue
                    if str(entry.get("id") or "") == rid:
                        if not echo_name:
                            echo_name = str(
                                entry.get("class_name") or ""
                            )
                        if not echo_time:
                            echo_time = str(
                                entry.get("start_time_local") or ""
                            )
                        break

            context.set_data("last_action", "canceled")

            # Update chosen_class for the dashboard. For a pure cancel
            # the canceled session is the verb target; for a
            # reschedule this is a transient state — the subsequent
            # create_reservation success block overwrites chosen_class
            # with the new session.
            existing = dict(context.get_data("chosen_class") or {})
            merged = {
                **existing,
                "name": echo_name or existing.get("name"),
                "start_time_local": (
                    echo_time or existing.get("start_time_local")
                ),
                "reservation_id": rid,
            }
            context.set_data("chosen_class", merged)

            # AC8 — if this cancel is the first leg of a reschedule
            # (caller intent = modify_reservation), stash the source
            # session so the next create_reservation can flip
            # last_action to "rescheduled" and the orphan-cancel path
            # has the canceled session's copy to narrate.
            if context.get_data("intent") == "modify_reservation":
                context.set_data(
                    "_pending_reschedule_source",
                    {
                        "reservation_id": rid,
                        "class_name": echo_name,
                        "start_time_local": echo_time,
                    },
                )

            # Scratch cleanup — once the cancel lands we no longer
            # need the narrated list as a disambiguation target.
            context.set_data("_last_listed_reservations", None)

            result = {
                **result,
                "class_name": echo_name,
                "start_time_local": echo_time,
            }

        # ----- Story 2.2 AC8/AC9 — create_reservation failed AFTER a
        # successful cancel (reschedule's second leg). Stash
        # _orphaned_cancel so the streaming narration turn (and the
        # dashboard) can surface exactly what landed. last_action
        # stays "canceled" because that verb is the most advanced
        # one that succeeded.
        if (
            name == "create_reservation"
            and result.get("success") is False
        ):
            pending = context.get_data("_pending_reschedule_source")
            if pending is not None:
                context.set_data("_orphaned_cancel", dict(pending))
                context.set_data("_pending_reschedule_source", None)
                # Do NOT touch last_action here — the prior cancel
                # success already set it to "canceled", which is the
                # correct final verb for the orphan state.

        # Reschedule second leg landed: clear the pending marker so
        # a subsequent turn doesn't mis-attribute a pure booking.
        if (
            name == "create_reservation"
            and result.get("success") is True
            and context.get_data("_pending_reschedule_source") is not None
        ):
            context.set_data("_pending_reschedule_source", None)

        messages.append(
            ToolResultMessage(
                tool_call_id=call.id,
                name=name,
                content=result,
            )
        )

    if any_stashed_options:
        # Stash AFTER the loop so we don't persist a partial list if a
        # subsequent tool errored mid-batch.
        context.set_data("_last_session_options", any_stashed_options)

    return payment_checked_this_turn


async def _run_first_turn_with_tools(context: Context, user_text: str) -> list:
    """Non-streaming first turn — inspect tool calls, dispatch, append
    result frames, return the updated ``messages`` list for the narration
    turn. Does NOT yield TTS — second turn owns that.

    Handles the code-side gate guard (AC3): if the model asks for
    ``list_class_sessions`` without prior eligibility, we inject a
    ``ToolResultMessage`` with an error payload and let the model
    recover on the narration turn.
    """
    messages = deserialize_conversation(context.get_completion_messages())
    messages.append(UserMessage(content=user_text))

    response = await generate_chat_completion(
        {
            "provider": LLM_PROVIDER,
            "model": LLM_MODEL,
            "messages": messages,
            "temperature": 0.3,
            "max_tokens": 200,
            "tools": TOOLS,
            "tool_choice": "auto",
        }
    )

    messages.append(response.message)

    tool_calls = getattr(response.message, "tool_calls", None) or []
    if not tool_calls:
        return messages

    cached_eligible = context.get_data("eligible", None)
    payment_checked_this_turn = cached_eligible is True
    await _dispatch_tool_calls(
        context, messages, tool_calls, payment_checked_this_turn
    )
    return messages


async def handler(event: Event, context: Context):
    """Main event handler (async generator)."""

    # Hydrate MT_* from context.variables on the first event; idempotent
    # thereafter. Must run before any tool/executor touches CONFIG or the
    # deployed runtime crashes with "Missing required env var: MT_BASE_URL"
    # (``.env.local`` is workspace-only and does not ship with ``vr push``).
    bootstrap_config(context)

    if isinstance(event, StartEvent):
        _ensure_completions_provider(context)
        _ensure_session_state(context)
        _ensure_system_prompt(context)
        yield TextToSpeechEvent(text=GREETING, voice=AGENT_VOICE)
        return

    if isinstance(event, TextEvent):
        user_text = (event.data.get("text") or "").strip()
        if not user_text:
            return

        _ensure_completions_provider(context)
        _ensure_session_state(context)
        _ensure_system_prompt(context)

        # Story 2.1 Task 2.6 — expose the raw caller utterance to
        # _dispatch_tool_calls so the pre-flight miss matcher can
        # detect a named spot being reported unavailable on the SAME
        # turn (before any create_reservation call).
        context.set_data("_last_user_text", user_text)

        # Per-turn state: cleared on entry, populated by
        # _dispatch_tool_calls on create_reservation success, consumed
        # below to short-circuit the streaming narration round.
        context.set_data("_booked_this_turn", False)
        # Per-turn state: populated by _dispatch_tool_calls when the
        # model calls the end_call tool. Stores the caller-facing
        # closing speech; consumed below to yield StopAction and
        # terminate the session.
        context.set_data("_end_call_requested", None)

        turn_id = _bump_turn(context)

        # Story 2.7 AC5 / AC8 — FAQ detour detection takes precedence
        # over modify/book so a caller asking "what are your hours?"
        # mid-booking flips intent to ``studio_info`` for THIS turn
        # without clearing any booking scratch (chosen_class,
        # _last_session_options, _last_listed_reservations, etc. all
        # remain intact — the next caller turn can resume booking
        # right where it left off). Modify/book detection still runs
        # as an ``elif`` so non-FAQ utterances keep Story 1.3 / 2.2
        # behavior verbatim.
        #
        # ``faq_turn`` is TURN-LOCAL — distinct from
        # ``context.get_data("intent")``, which persists turn-to-turn
        # until a detector fires again. We use the local flag to gate
        # behavior that must apply ONLY on the current FAQ utterance
        # (resolver skip; do-not-clobber booking scratch), while the
        # persisted ``intent`` is used by the model / prompt copy.
        #
        # Story 2.2 AC6 — modify-intent detection takes precedence
        # over the Story 1.3 book_class default ("Cancel my class"
        # would trivially match both; cancel wins). Intent is
        # informational — the tools themselves gate which MT calls
        # actually fire; we don't branch on it to suppress Epic 1
        # tools.
        faq_turn = _looks_like_faq(user_text)
        if faq_turn:
            context.set_data("intent", "studio_info")
        elif _looks_like_modify(user_text):
            context.set_data("intent", "modify_reservation")
        elif _looks_like_book_class(user_text):
            context.set_data("intent", "book_class")

        # AC9 — reset last_action at the top of every modify turn so
        # a stale "booked" from a prior booking doesn't bleed into
        # the next cancel/reschedule confirmation turn.
        if context.get_data("intent") == "modify_reservation":
            context.set_data("last_action", None)

        # Turn-local scratch reset. _pending_reschedule_source /
        # _orphaned_cancel are populated inside _dispatch_tool_calls
        # and consumed by the streaming narration + booking_progress
        # emitter. We keep the reschedule marker across turns when the
        # prior turn's cancel has not yet been paired with a create —
        # e.g. the caller said "cancel my noon" on turn N and now says
        # "yes, book the 7" on turn N+1. That follow-up confirmation
        # still belongs to the reschedule flow and should land as
        # ``last_action="rescheduled"`` when create succeeds. Only
        # clear the marker when the new turn is clearly a fresh flow
        # (no cancel in hand). _orphaned_cancel is always per-turn:
        # the narration happens in the same turn we detect the orphan.
        if context.get_data("last_action") != "canceled":
            context.set_data("_pending_reschedule_source", None)
        context.set_data("_orphaned_cancel", None)

        # Resolve follow-ups against the PREVIOUS turn's narration
        # before re-entering the tool loop. Prevents the model from
        # needlessly re-fetching when the caller just said "yeah, the 6".
        #
        # Story 2.7 AC8 — skipped on FAQ turns so an utterance like
        # "are you open at 6?" does not accidentally commit a
        # ``chosen_class`` or wipe ``_last_session_options`` mid-
        # detour. Booking state must survive the FAQ turn verbatim;
        # the next non-FAQ utterance re-enters the resolver (we key
        # on the TURN-LOCAL ``faq_turn`` flag, not the persisted
        # ``intent``, so a post-detour utterance like "book me for
        # the 6" still resolves even while intent is still
        # ``studio_info`` from the prior turn).
        if not faq_turn:
            _resolve_chosen_class(context, user_text)

        messages = await _run_first_turn_with_tools(context, user_text)

        # end_call short-circuit (first-turn path). If Devin called
        # the end_call tool on the non-streaming first turn,
        # _dispatch_tool_calls stashed the closing_speech on Context.
        # Yield a single StopAction with that speech and return —
        # skipping the streaming narration round, BOOKING_CONFIRMATION,
        # booking_progress, and SILENCE_FALLBACK entirely. The runtime
        # plays closing_speech before terminating the session.
        end_speech = context.get_data("_end_call_requested", None)
        if end_speech:
            context.set_completion_messages(messages)
            yield StopAction(
                closing_speech=str(end_speech), voice=AGENT_VOICE
            )
            return

        # Second turn — stream the final narration. Provide tools again
        # because Gemini sometimes splits tool calls across turns (e.g.
        # round 1 fires ``check_payment_options``, the streaming round
        # then fires ``list_class_sessions`` or ``create_reservation``).
        # When the streaming round emits tool_calls instead of text,
        # dispatch them and restart the stream — capped at
        # ``MAX_STREAM_ROUNDS`` to prevent run-away loops. Bound chosen
        # empirically: the longest sanctioned path is
        # list+create in a single streaming response, recovered from in
        # one extra round.
        MAX_STREAM_ROUNDS = 3
        # Live-call regression: Gemini sometimes exits the streaming
        # loop with zero content_sentence chunks — either because the
        # model chose silence ("nothing to add" after a completed
        # booking) or because MAX_STREAM_ROUNDS was exhausted while
        # still tool-calling. Without a safety net the caller hears
        # dead air until TIMEOUT_NUDGE fires 3 s later. Track whether
        # we yielded any TTS and emit SILENCE_FALLBACK at the end if
        # not. Cheaper than adding a heartbeat or a watchdog task.
        spoke_this_turn = False

        # Deterministic closer override: if create_reservation already
        # succeeded during the non-streaming first turn, skip the
        # streaming narration entirely and yield BOOKING_CONFIRMATION
        # verbatim. This is the common happy path (Gemini usually
        # fires check_payment_options + list_class_sessions OR
        # create_reservation in a single first-turn response), so
        # short-circuiting here also saves an LLM round-trip.
        if context.get_data("_booked_this_turn", False):
            yield TextToSpeechEvent(
                text=BOOKING_CONFIRMATION, voice=AGENT_VOICE
            )
            spoke_this_turn = True

        for _ in range(MAX_STREAM_ROUNDS):
            # Second short-circuit point: the booking could instead
            # land on a streaming round (Gemini sometimes splits tool
            # calls — list_class_sessions first, create_reservation
            # after). If the previous round's dispatch flipped the
            # flag, stop streaming, yield the canonical closer, and
            # let any preamble the model already spoke stand.
            if context.get_data("_booked_this_turn", False) and (
                not spoke_this_turn
            ):
                yield TextToSpeechEvent(
                    text=BOOKING_CONFIRMATION, voice=AGENT_VOICE
                )
                spoke_this_turn = True
                break
            elif context.get_data("_booked_this_turn", False):
                # Already yielded (first-turn path). Nothing more to
                # say this turn.
                break

            stream = await generate_chat_completion_stream(
                request={
                    "provider": LLM_PROVIDER,
                    "model": LLM_MODEL,
                    "messages": messages,
                    "temperature": 0.4,
                    "max_tokens": 140,
                    "tools": TOOLS,
                    "tool_choice": "auto",
                },
                stream_options={
                    "stream_sentences": True,
                    "clean_sentences": True,
                },
            )

            last_response = None
            async for chunk in stream:
                ctype = getattr(chunk, "type", None)
                if ctype == "content_sentence":
                    sentence = (getattr(chunk, "sentence", "") or "").strip()
                    if sentence:
                        yield TextToSpeechEvent(
                            text=sentence, voice=AGENT_VOICE
                        )
                        spoke_this_turn = True
                elif ctype == "response":
                    last_response = chunk.response
                    messages.append(chunk.response.message)

            stream_tool_calls = (
                getattr(last_response.message, "tool_calls", None) or []
                if last_response is not None
                else []
            )
            if not stream_tool_calls:
                break

            cached_eligible = context.get_data("eligible", None)
            payment_checked_this_turn = cached_eligible is True
            await _dispatch_tool_calls(
                context,
                messages,
                stream_tool_calls,
                payment_checked_this_turn,
            )

            # end_call short-circuit (streaming-round path). Gemini
            # occasionally splits calls — e.g. narrates a booking
            # confirmation on the first streaming round, then calls
            # end_call on the next. Break out of the streaming loop as
            # soon as end_call lands so we don't restart the stream
            # unnecessarily. The StopAction yield happens below the
            # loop (shared with the first-turn short-circuit below so
            # any already-spoken sentences this turn are preserved).
            if context.get_data("_end_call_requested", None):
                break

        end_speech = context.get_data("_end_call_requested", None)
        if end_speech:
            context.set_completion_messages(messages)
            yield StopAction(
                closing_speech=str(end_speech), voice=AGENT_VOICE
            )
            return

        if not spoke_this_turn:
            # Last-line defense against the dead-air bug. Yield a
            # gentle, non-committal nudge so the caller knows we
            # heard them and the turn ends cleanly.
            yield TextToSpeechEvent(
                text=SILENCE_FALLBACK, voice=AGENT_VOICE
            )

        # Clear the per-turn booking flag after use — belt-and-
        # suspenders with the TextEvent-entry clear above.
        context.set_data("_booked_this_turn", False)

        context.set_completion_messages(messages)

        # Caller confirmations often land in the SAME turn as the model's
        # narration ("I'll take the 6" piggybacked on the intro), so run
        # the resolver once more after the reply completes. Harmless if
        # nothing matches. Skipped on FAQ turns (see pre-call note) —
        # the detour must not overwrite booking scratch even post-reply.
        if not faq_turn:
            _resolve_chosen_class(context, user_text)

        yield _booking_progress_event(context, turn_id)
        return

    if isinstance(event, TimeoutEvent):
        count = event.data.get("count", 0)
        if count >= TIMEOUT_MAX_COUNT:
            yield StopAction(closing_speech=TIMEOUT_HANGUP, voice=AGENT_VOICE)
            return
        if count in (3, 6):
            yield TextToSpeechEvent(text=TIMEOUT_NUDGE, voice=AGENT_VOICE)
        return

    if isinstance(event, StopEvent):
        yield TextToSpeechEvent(text=GOODBYE, voice=AGENT_VOICE)
        return


# Silence unused-import lint — MAX_NARRATED_SESSIONS is re-exported for
# tests and future intra-package consumers.
_ = MAX_NARRATED_SESSIONS
