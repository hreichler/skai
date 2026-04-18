"""Mariana Tek tool wiring — Stories 1.3 + 1.4 + 2.1 + 2.2.

Exposes four `primfunctions.completions.ToolDefinition`s the handler
hands to the model on each tool-loop turn:

* ``check_payment_options`` — **gate**: does the caller actually have a
  way to pay for a class (active membership, remaining credit pack)?
  MUST be called before narrating class times. Returns a compact
  ``{eligible, credits_remaining, reason}`` dict.
* ``list_class_sessions`` — **discovery**: top 2–3 upcoming sessions
  inside a date window. Safe to call only after the gate returns
  ``eligible=true``.
* ``discover_spots`` — **layout lookup** (Story 2.1): fetches the
  classroom layout for a chosen class_session (JSON:API ``?include=
  spots``) so the model can map a caller utterance ("Bike 5") to a
  concrete ``spot_id``. Returns ``{spots: [{id, label, is_available,
  neighbor_ids}], count}``. Availability is intersection of the
  layout with ``class_session.attributes.available_spots``;
  ``neighbor_ids`` are resolved deterministically (positional first,
  numeric-from-label fallback, ``[]`` last) so the recovery path can
  suggest an adjacent spot without guessing.
* ``create_reservation`` — **execution** (Story 1.4): books the caller
  into a specific ``class_session_id`` once they've verbally confirmed.
  Returns ``{success, reservation_id, class_name, start_time_local}``
  on 201, or ``{success: false, error, message}`` on any MT failure.
  The happy-path ``class_name`` / ``start_time_local`` are echo fields
  (MT's POST response does not include them verbatim) so the model's
  confirmation sentence reads naturally.
* ``list_user_reservations`` — **modify lookup** (Story 2.2): returns
  the demo user's upcoming, non-canceled reservations so the model
  can resolve "my 6 PM" into a concrete ``reservation_id`` before
  calling ``cancel_reservation``. Shape:
  ``{reservations: [{id, class_name, start_time_local, spot_label}],
  count}`` sorted by ``start_time_local`` ascending. Past
  reservations are dropped client-side.
* ``cancel_reservation`` — **modify execution** (Story 2.2): cancels
  one reservation by id. Probes ``POST /reservations/{id}/cancel``
  first; falls back to ``PATCH /reservations/{id}`` with
  ``status=canceled`` when the sandbox refuses the action path. The
  reschedule flow is composed caller-side by the model: cancel first,
  then ``create_reservation``. Errors are mapped to compact codes
  (``already_canceled`` / ``not_owner`` / ``past_class`` /
  ``lookup_failed``) that the model narrates.

## Deviations from the story text (documented here + in Dev Agent Record)

1. MT's ``/payment_options/`` endpoint **requires** both ``class_session``
   and ``user`` query params (verified live against
   ``https://reseat.sandbox.marianatek.com/api/payment_options/``). A
   user-only call returns ``422 {"errors":{"class_session":"A class
   session must be specified…"}}``. To keep the story's LLM-visible
   contract ("call ``check_payment_options`` with no args"), this module
   picks a probe class session internally (next public upcoming session
   from a ``page_size=1`` ``class_sessions`` fetch) and calls
   ``/payment_options/`` with that ``class_session`` plus the configured
   demo user. NFR2's "hardcoded Demo User" lives in
   ``CONFIG.mt_demo_user_id`` (env: ``MT_DEMO_USER_ID``).

2. Story AC1 parameter schema says ``user_id: string`` — we accept that
   arg for future flexibility but the hackathon default is ``CONFIG.
   mt_demo_user_id``. Not required.

3. ``ToolResultMessage.content`` in the installed ``primfunctions``
   runtime is typed ``dict[str, Any]`` (not a JSON string). The story
   Task 2.2 hint ``json.dumps(result)`` would mis-type. Executors here
   return dicts; handler passes them straight through.

4. ``FunctionCall.arguments`` arrives as a ``dict`` (already parsed by
   the completions client). No ``json.loads`` needed on the handler
   side.

Single dep constraint (Story 1.3 AC10, Epic 1 Build-Constraints): only
``primfunctions`` appears in ``requirements.txt``. All HTTP goes through
stdlib ``urllib`` wrapped in ``asyncio.to_thread`` so the handler's
async contract is preserved.

No ``booking_progress`` / ``tool_call_start`` DebugEvents are emitted
from this module — Story 2.4 owns mid-stream tool-call telemetry, and
``booking_progress`` is emitted by the handler (once per turn) per
``docs/schema.md``.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Optional
from zoneinfo import ZoneInfo

from primfunctions.completions import FunctionDefinition, ToolDefinition

from config import CONFIG


# ---------------------------------------------------------------------------
# Story 2.7 — static studio knowledge blob (FAQ answers)
# ---------------------------------------------------------------------------
#
# ``studio_knowledge.json`` lives alongside this module so ``vr push``
# bundles it automatically. We load it ONCE at import time into a
# module-level dict — per-call I/O would be wasteful (and racy on the
# deployed runtime). The file is flat ``{topic: sentence}`` per the
# story's AC1 contract; values are TTS-ready one-sentence strings.
#
# Failure modes are absorbed here so the executor never raises:
#   * File missing → empty dict, one-line stderr warning, executor
#     returns ``{error: "knowledge_unavailable"}`` on every call.
#   * Malformed JSON / non-string value → same degrade path.
# This mirrors the ``MTHttpError`` → compact-error pattern used by the
# live MT tools so the handler's streaming narration can apologize
# gracefully rather than crashing the module at import.

STUDIO_KNOWLEDGE_FILENAME = "studio_knowledge.json"
FAQ_TOPICS = ("hours", "pricing", "amenities", "location", "class_types")


def _load_studio_knowledge() -> dict[str, str]:
    path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), STUDIO_KNOWLEDGE_FILENAME
    )
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, json.JSONDecodeError) as err:
        print(
            f"[tools] WARN: could not load {STUDIO_KNOWLEDGE_FILENAME}: {err}",
            file=sys.stderr,
        )
        return {}
    if not isinstance(raw, dict):
        print(
            f"[tools] WARN: {STUDIO_KNOWLEDGE_FILENAME} is not a JSON object",
            file=sys.stderr,
        )
        return {}
    cleaned: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(value, str):
            continue
        cleaned[str(key).strip().lower()] = value.strip()
    return cleaned


_STUDIO_KNOWLEDGE: dict[str, str] = _load_studio_knowledge()


# ---------------------------------------------------------------------------
# HTTP — Mariana Tek JSON:API client (stdlib only)
# ---------------------------------------------------------------------------

MT_ACCEPT = "application/vnd.api+json"
MT_CONTENT_TYPE = "application/vnd.api+json"
MT_READ_TIMEOUT_S = 5.0
MAX_NARRATED_SESSIONS = 3  # AC4 "at most 3 options"
DEFAULT_LOOKAHEAD_DAYS = 7
# The reseat sandbox is a single-brand fixture where every location
# reports ``timezone: "US/Eastern"`` (probed via ``/locations``). We use
# it to convert class_sessions' UTC ``start_datetime`` into a
# caller-friendly local string. Story 2.5 can look up per-session tz
# from the related ``location`` resource if the sandbox ever expands to
# multi-tz brands.
STUDIO_TZ = ZoneInfo("US/Eastern")


@dataclass(frozen=True)
class MTHttpError(Exception):
    """Raised when MT returns non-2xx; executors catch this and degrade."""

    status: int
    path: str
    body_head: str

    def __str__(self) -> str:  # pragma: no cover — diagnostic only
        return f"MT {self.status} @ {self.path}: {self.body_head[:160]}"


def _mt_get_sync(path: str, params: Optional[dict] = None) -> dict:
    """Blocking GET against Mariana Tek. Keep off the event loop via
    ``asyncio.to_thread``; never call directly from the handler.
    """
    query = f"?{urllib.parse.urlencode(params)}" if params else ""
    url = f"{CONFIG.mt_base_url.rstrip('/')}{path}{query}"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {CONFIG.mt_access_token}",
            "Accept": MT_ACCEPT,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=MT_READ_TIMEOUT_S) as resp:
            body = resp.read()
    except urllib.error.HTTPError as err:
        raise MTHttpError(
            status=err.code,
            path=path,
            body_head=(err.read() or b"")[:200].decode("utf-8", "replace"),
        ) from err
    except urllib.error.URLError as err:  # pragma: no cover — network flake
        raise MTHttpError(status=0, path=path, body_head=str(err.reason)) from err
    return json.loads(body.decode("utf-8"))


async def _mt_get(path: str, params: Optional[dict] = None) -> dict:
    """Async wrapper around :func:`_mt_get_sync`."""
    return await asyncio.to_thread(_mt_get_sync, path, params)


def _mt_post_sync(path: str, body: dict) -> dict:
    """Blocking POST against Mariana Tek (JSON:API). Mirrors
    :func:`_mt_get_sync` — same Bearer + Accept headers, plus a
    ``Content-Type: application/vnd.api+json`` and a JSON-encoded body.
    2xx returns the parsed response; non-2xx raises :class:`MTHttpError`.
    """
    url = f"{CONFIG.mt_base_url.rstrip('/')}{path}"
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {CONFIG.mt_access_token}",
            "Accept": MT_ACCEPT,
            "Content-Type": MT_CONTENT_TYPE,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=MT_READ_TIMEOUT_S) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as err:
        raise MTHttpError(
            status=err.code,
            path=path,
            body_head=(err.read() or b"")[:200].decode("utf-8", "replace"),
        ) from err
    except urllib.error.URLError as err:  # pragma: no cover — network flake
        raise MTHttpError(status=0, path=path, body_head=str(err.reason)) from err
    if not raw:
        return {}
    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError:
        return {}


async def _mt_post(path: str, body: dict) -> dict:
    """Async wrapper around :func:`_mt_post_sync`."""
    return await asyncio.to_thread(_mt_post_sync, path, body)


def _mt_patch_sync(path: str, body: dict) -> dict:
    """Blocking PATCH against Mariana Tek (JSON:API). Used by
    Story 2.2's cancel fallback when the sandbox rejects the
    action-style ``POST /reservations/{id}/cancel`` (some MT installs
    only accept ``PATCH /reservations/{id}`` with
    ``attributes.status = "canceled"``).
    """
    url = f"{CONFIG.mt_base_url.rstrip('/')}{path}"
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="PATCH",
        headers={
            "Authorization": f"Bearer {CONFIG.mt_access_token}",
            "Accept": MT_ACCEPT,
            "Content-Type": MT_CONTENT_TYPE,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=MT_READ_TIMEOUT_S) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as err:
        raise MTHttpError(
            status=err.code,
            path=path,
            body_head=(err.read() or b"")[:200].decode("utf-8", "replace"),
        ) from err
    except urllib.error.URLError as err:  # pragma: no cover — network flake
        raise MTHttpError(status=0, path=path, body_head=str(err.reason)) from err
    if not raw:
        return {}
    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError:
        return {}


async def _mt_patch(path: str, body: dict) -> dict:
    """Async wrapper around :func:`_mt_patch_sync`."""
    return await asyncio.to_thread(_mt_patch_sync, path, body)


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _today_local_iso() -> str:
    """``YYYY-MM-DD`` for *today* in the studio's timezone."""
    return datetime.now(STUDIO_TZ).date().isoformat()


def _future_iso(days: int) -> str:
    return (datetime.now(STUDIO_TZ).date() + timedelta(days=days)).isoformat()


def _format_session_time(attrs: dict) -> str:
    """Return a caller-friendly string like ``"Mon 5:00 PM"`` in the
    studio's local timezone (``STUDIO_TZ``).

    Prefer ``start_datetime`` (ISO8601 UTC per MT); fall back to
    ``start_date`` + ``start_time`` assumed local. Windows/Mac/Linux
    all choke on ``%-I`` so we use ``%I`` and strip the leading zero
    manually.
    """
    raw = attrs.get("start_datetime") or ""
    dt: Optional[datetime] = None
    if raw:
        try:
            # ``fromisoformat`` accepts "2026-04-20T16:00:00+00:00" but not
            # the trailing "Z" MT uses. Swap Z → +00:00 first.
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            dt = None
    if dt is None:
        d_raw = attrs.get("start_date") or ""
        t_raw = attrs.get("start_time") or ""
        try:
            dt = datetime.fromisoformat(f"{d_raw}T{t_raw}").replace(
                tzinfo=STUDIO_TZ
            )
        except ValueError:
            return (raw or f"{d_raw} {t_raw}").strip() or "TBD"

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local = dt.astimezone(STUDIO_TZ)
    weekday = local.strftime("%a")
    clock = local.strftime("%I:%M %p").lstrip("0")
    return f"{weekday} {clock}"


def _session_summary(s: dict) -> dict:
    """Flatten a class_sessions ``data`` entry into the compact shape the
    LLM will see (AC6). Intentionally discards JSON:API plumbing and
    reservation-count fields — those are noise in voice-only output.
    """
    attrs = s.get("attributes", {}) or {}
    names = attrs.get("instructor_names") or []
    name_label = attrs.get("class_type_display") or "Class"
    if names:
        name_label = f"{name_label} w/ {names[0]}"
    return {
        "id": str(s.get("id")),
        "name": name_label,
        "start_time_local": _format_session_time(attrs),
        "location": attrs.get("location_display") or "",
    }


# ---------------------------------------------------------------------------
# Executors
# ---------------------------------------------------------------------------

async def execute_check_payment_options(args: dict) -> dict:
    """Story 1.3 gate (AC1, AC6).

    Returns ``{"eligible": bool, "credits_remaining": int | None,
    "reason": str | None}`` where ``reason`` is one of ``None`` /
    ``"no_active_credits"`` / ``"expired"`` / ``"lookup_failed"``.

    Internal flow:
      1. Probe: ``GET /class_sessions?page_size=1&min_date=today`` → pick
         the first upcoming session's id.
      2. Gate: ``GET /payment_options/?class_session=<sid>&user=<uid>``.
      3. Count ``is_active and not error_message`` entries; sum the
         ``count`` attribute for ``payment_option_type == "credit"``.

    Args:
      ``user_id`` (optional) — override ``CONFIG.mt_demo_user_id``.
    """
    user_id = str(args.get("user_id") or CONFIG.mt_demo_user_id)
    try:
        # Step 1 — probe an upcoming class to anchor the payment_options query.
        probe = await _mt_get(
            "/class_sessions",
            {"min_date": _today_local_iso(), "page_size": 1},
        )
        probe_data = probe.get("data") or []
        if not probe_data:
            # No upcoming classes in the sandbox at all — narrating options
            # is moot, but the gate shouldn't falsely say ineligible. Let
            # the model handle this via the ineligible copy path.
            return {
                "eligible": False,
                "credits_remaining": None,
                "reason": "no_upcoming_classes",
            }
        probe_sid = str(probe_data[0].get("id"))

        # Step 2 — real gate call. MT requires both class_session and user.
        resp = await _mt_get(
            "/payment_options/",
            {"class_session": probe_sid, "user": user_id},
        )
    except MTHttpError:
        # Do not crash the call. Treat unknown-eligibility as ineligible so
        # the agent narrates the "let me call you back" copy rather than
        # leaking dead air. AC5 / AC6 ``reason=lookup_failed``.
        return {
            "eligible": False,
            "credits_remaining": None,
            "reason": "lookup_failed",
        }

    options = resp.get("data") or []
    active = [
        o for o in options
        if (o.get("attributes") or {}).get("is_active")
        and not (o.get("attributes") or {}).get("error_message")
    ]
    if not active:
        reason = "no_active_credits" if not options else "expired"
        return {"eligible": False, "credits_remaining": 0, "reason": reason}

    credits_remaining = sum(
        int((o.get("attributes") or {}).get("count") or 0)
        for o in active
        if (o.get("attributes") or {}).get("payment_option_type") == "credit"
    )
    return {
        "eligible": True,
        "credits_remaining": credits_remaining or None,
        "reason": None,
    }


async def execute_list_class_sessions(args: dict) -> dict:
    """Story 1.3 discovery (AC2, AC6).

    Returns ``{"sessions": [{id, name, start_time_local, location}],
    "count": int}`` with at most ``MAX_NARRATED_SESSIONS`` entries.
    Accepts optional ``min_date`` / ``max_date`` in ``YYYY-MM-DD``; both
    default to ``today`` and ``today+7`` when absent.
    """
    min_date = args.get("min_date") or _today_local_iso()
    max_date = args.get("max_date") or _future_iso(DEFAULT_LOOKAHEAD_DAYS)
    try:
        resp = await _mt_get(
            "/class_sessions",
            {
                "min_date": min_date,
                "max_date": max_date,
                "page_size": MAX_NARRATED_SESSIONS * 3,  # over-fetch; filter below
            },
        )
    except MTHttpError:
        return {"sessions": [], "count": 0, "error": "lookup_failed"}

    raw = resp.get("data") or []
    # Prefer sessions that still have spots and are public. ``available_spots``
    # is a list of spot ids; empty means sold out (we skip — voice agent
    # can't usefully narrate a class no one can book).
    filtered: list[dict] = []
    for s in raw:
        a = s.get("attributes") or {}
        if not a.get("public", True):
            continue
        if not (a.get("available_spots") or []):
            continue
        filtered.append(s)
        if len(filtered) >= MAX_NARRATED_SESSIONS:
            break

    sessions = [_session_summary(s) for s in filtered]
    return {"sessions": sessions, "count": len(sessions)}


# ---------------------------------------------------------------------------
# Story 2.1 — classroom layout helpers + discover_spots executor
# ---------------------------------------------------------------------------

_CYCLING_TOKENS = ("ride", "cycle", "spin")


def _class_type_prefix(class_type_display: str) -> str:
    """Infer the caller-friendly prefix for a positional label.

    Cycling classes say "Bike"; everything else falls back to generic
    "Spot" (covers reformer beds, mat reservations, etc.). AC3 rule 2.
    """
    lowered = (class_type_display or "").lower()
    for tok in _CYCLING_TOKENS:
        if tok in lowered:
            return "Bike"
    return "Spot"


def _spot_label(attrs: dict, raw_id: str, prefix: str) -> str:
    """Resolve the caller-facing label per AC3 precedence.

    1. ``attrs.name`` if present and non-empty (e.g. ``"Bike 5"``).
    2. ``attrs.spot_number`` with class-type prefix.
    3. Fall back to ``f"Spot #{raw_id}"`` so the tool still yields a
       usable label.
    """
    name = attrs.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    spot_number = attrs.get("spot_number")
    if spot_number is not None and str(spot_number).strip():
        return f"{prefix} {spot_number}"
    return f"Spot #{raw_id}"


_LABEL_NUMBER_RE = re.compile(r"(\d+)")


def _label_number(label: str) -> Optional[int]:
    """Parse the trailing integer from a label like ``"Bike 5"`` → 5.
    Returns ``None`` if no digits are present (the numeric-fallback
    adjacency path simply gives up on that spot).
    """
    if not label:
        return None
    match = _LABEL_NUMBER_RE.search(label)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:  # pragma: no cover — regex guarantees digits
        return None


def _resolve_neighbors(all_spots: list[dict], me: dict) -> list[str]:
    """Return up to two adjacent spot ids per AC4.

    Positional path first (``x_pos`` / ``y_pos`` differ by exactly 1
    unit on a single axis), numeric-from-label path second, ``[]`` last.
    Caller-facing "left / right" preferred when horizontal neighbors
    exist; otherwise any positional neighbor or the numeric ±1 pair.
    """
    me_attrs = me.get("attributes") or {}
    me_id = str(me.get("id") or "")

    def _coord(attrs: dict, key: str) -> Optional[float]:
        val = attrs.get(key)
        if val is None:
            return None
        try:
            return float(val)
        except (TypeError, ValueError):
            return None

    my_x = _coord(me_attrs, "x_pos")
    my_y = _coord(me_attrs, "y_pos")

    if my_x is not None and my_y is not None:
        horizontal: list[str] = []
        vertical: list[str] = []
        for other in all_spots:
            other_id = str(other.get("id") or "")
            if other_id == me_id:
                continue
            other_attrs = other.get("attributes") or {}
            ox = _coord(other_attrs, "x_pos")
            oy = _coord(other_attrs, "y_pos")
            if ox is None or oy is None:
                continue
            dx = ox - my_x
            dy = oy - my_y
            if abs(dx) == 1 and dy == 0:
                horizontal.append(other_id)
            elif abs(dy) == 1 and dx == 0:
                vertical.append(other_id)
        if horizontal:
            return horizontal[:2]
        if vertical:
            return vertical[:2]

    my_num = _label_number(str(me_attrs.get("name") or "")) or _label_number(
        str(me_attrs.get("spot_number") or "")
    )
    if my_num is not None:
        numeric_neighbors: list[str] = []
        for other in all_spots:
            other_id = str(other.get("id") or "")
            if other_id == me_id:
                continue
            other_attrs = other.get("attributes") or {}
            other_num = _label_number(str(other_attrs.get("name") or "")) or (
                _label_number(str(other_attrs.get("spot_number") or ""))
            )
            if other_num is None:
                continue
            if abs(other_num - my_num) == 1:
                numeric_neighbors.append(other_id)
        return numeric_neighbors[:2]

    return []


async def execute_discover_spots(args: dict) -> dict:
    """Story 2.1 layout lookup (AC1–AC4).

    ``GET /class_sessions/{id}/?include=layout.spots`` (JSON:API
    sideloading). MT's ``class_session`` resource does NOT expose a
    direct ``spots`` relationship — spots hang off the session's
    ``layout``. Requesting ``include=spots`` 422s with
    ``"spots" is not a valid field name for "class_session"``. The
    ``layout.spots`` chain sideloads both the ``layouts`` and ``spots``
    entries; we filter ``included`` by ``type == "spots"``.

    Returns ``{"spots": [{id, label, is_available, neighbor_ids}],
    "count": int}``. Missing/empty ``class_session_id`` or any non-2xx
    returns ``{"spots": [], "count": 0, "error": "lookup_failed"}`` —
    never raises. Availability is the intersection of the classroom
    layout (every spot in the ``included`` array) with
    ``class_session.attributes.available_spots`` (list of ids). Raw
    JSON:API attributes are NOT forwarded — only the compact shape
    above, sorted by label for deterministic narration order.
    """
    class_session_id = str(args.get("class_session_id") or "").strip()
    if not class_session_id:
        return {"spots": [], "count": 0, "error": "lookup_failed"}

    try:
        resp = await _mt_get(
            f"/class_sessions/{class_session_id}/",
            {"include": "layout.spots"},
        )
    except MTHttpError:
        return {"spots": [], "count": 0, "error": "lookup_failed"}

    primary = resp.get("data") or {}
    primary_attrs = primary.get("attributes") or {}
    available_raw = primary_attrs.get("available_spots") or []
    available_ids: set[str] = {str(x) for x in available_raw if x is not None}
    prefix = _class_type_prefix(str(primary_attrs.get("class_type_display") or ""))

    included = resp.get("included") or []
    raw_spots = [entry for entry in included if entry.get("type") == "spots"]

    spots: list[dict] = []
    for entry in raw_spots:
        raw_id = str(entry.get("id") or "").strip()
        if not raw_id:
            continue
        attrs = entry.get("attributes") or {}
        label = _spot_label(attrs, raw_id, prefix)
        neighbor_ids = _resolve_neighbors(raw_spots, entry)
        spots.append(
            {
                "id": raw_id,
                "label": label,
                "is_available": raw_id in available_ids,
                "neighbor_ids": neighbor_ids,
            }
        )

    spots.sort(key=lambda s: s["label"].lower())
    return {"spots": spots, "count": len(spots)}


def _map_reservation_error(err: MTHttpError) -> tuple[str, str]:
    """Map an :class:`MTHttpError` from ``POST /reservations/`` to a
    compact ``(error_code, short_message)`` pair the model can narrate.

    Story 1.4 AC4 codes: ``spot_unavailable`` / ``class_full`` /
    ``not_eligible`` / ``lookup_failed``. Keep messages under ~80 chars
    — the agent speaks them verbatim in the apology sentence.
    """
    status = err.status
    body_lc = (err.body_head or "").lower()

    # ``spot_unavailable`` is routed here so Story 2.1 can extend the
    # conversational recovery without re-plumbing the error mapping.
    if status == 409 or "spot" in body_lc:
        return "spot_unavailable", "that spot just went"
    if "full" in body_lc or "capacity" in body_lc:
        return "class_full", "that class is full"
    if status in (401, 403) or "eligib" in body_lc:
        return "not_eligible", "that class isn't bookable right now"
    return "lookup_failed", "I couldn't complete that booking"


async def execute_create_reservation(args: dict) -> dict:
    """Story 1.4 execution step (AC1, AC2, AC3, AC4).

    Builds a JSON:API reservation body for the demo user and POSTs it
    to ``{MT_BASE_URL}/reservations/``. On 201 returns a compact
    ``{success, reservation_id, class_name, start_time_local}``; on any
    non-2xx returns ``{success: false, error, message}`` with one of
    four routed error codes (``spot_unavailable`` / ``class_full`` /
    ``not_eligible`` / ``lookup_failed``).

    ``class_name`` and ``start_time_local`` are echo fields — MT's POST
    response does not include them verbatim. The model passes them in
    (from its prior ``list_class_sessions`` narration) so the spoken
    confirmation reads naturally; the handler backfills them from
    ``chosen_class`` if the model omits them (see handler.py).

    AC7 precedence: the caller-supplied ``class_session_id`` is the
    source of truth for the MT POST. The handler may observe a
    mismatch against ``context.chosen_class.id`` (model hallucination
    / caller-switched mid-utterance) and choose to log it, but this
    executor never second-guesses its args.
    """
    class_session_id = str(args.get("class_session_id") or "").strip()
    if not class_session_id:
        return {
            "success": False,
            "error": "lookup_failed",
            "message": "missing class_session_id",
        }

    relationships: dict = {
        "class_session": {
            "data": {"type": "class_sessions", "id": class_session_id}
        },
        "user": {
            "data": {"type": "users", "id": str(CONFIG.mt_demo_user_id)}
        },
    }

    spot_id = str(args.get("spot_id") or "").strip()
    if spot_id:
        # Story 2.1 exercises this branch; 1.4 leaves it wired but
        # unused by default (MT auto-assigns a spot on class types
        # that require one).
        relationships["spot"] = {"data": {"type": "spots", "id": spot_id}}

    body = {
        "data": {
            "type": "reservations",
            "attributes": {"reservation_type": "standard"},
            "relationships": relationships,
        }
    }

    try:
        resp = await _mt_post("/reservations/", body)
    except MTHttpError as err:
        code, msg = _map_reservation_error(err)
        return {"success": False, "error": code, "message": msg}

    data = resp.get("data") or {}
    reservation_id = str(data.get("id") or "").strip()
    if not reservation_id:
        # 2xx without an id is a shape drift — treat as lookup_failed
        # so the model apologizes rather than speaking a phantom
        # confirmation.
        return {
            "success": False,
            "error": "lookup_failed",
            "message": "reservation id missing from MT response",
        }

    return {
        "success": True,
        "reservation_id": reservation_id,
        "class_name": str(args.get("class_name") or ""),
        "start_time_local": str(args.get("start_time_local") or ""),
    }


# ---------------------------------------------------------------------------
# Story 2.2 — modify-reservation helpers (list + cancel)
# ---------------------------------------------------------------------------


def _parse_start_datetime(attrs: dict) -> Optional[datetime]:
    """Return a timezone-aware ``datetime`` for the earliest start-ish
    field on a ``class_sessions`` attrs block, or ``None`` if nothing
    parses. Tolerates the handful of field name / format variants the
    MT sandbox uses across installs.
    """
    for key in (
        "start_datetime",
        "start_datetime_local",
        "start_at",
        "start_time",
    ):
        raw = attrs.get(key)
        if not raw:
            continue
        try:
            dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            continue
        if dt.tzinfo is None:
            # Assume studio-local for naive timestamps; it's the closest
            # thing to ground truth the sandbox gives us.
            dt = dt.replace(tzinfo=STUDIO_TZ)
        return dt
    # Fallback: ``start_date`` + ``start_time`` pair (studio-local).
    d_raw = attrs.get("start_date") or ""
    t_raw = attrs.get("start_time") or ""
    if d_raw and t_raw:
        try:
            return datetime.fromisoformat(f"{d_raw}T{t_raw}").replace(
                tzinfo=STUDIO_TZ
            )
        except ValueError:
            return None
    return None


async def execute_list_user_reservations(args: dict) -> dict:
    """Story 2.2 modify-lookup (AC1, AC2).

    Returns ``{"reservations": [{id, class_name, start_time_local,
    spot_label}], "count": int}`` sorted by ``start_time_local``
    ascending. Only upcoming (``start_datetime`` in the future), non-
    canceled reservations for the configured demo user are returned.
    Deduped by reservation id. On any non-2xx returns
    ``{"reservations": [], "count": 0, "error": "lookup_failed"}`` —
    the handler gates on ``count`` so the model can apologize rather
    than hang.

    No required args — the demo user id is read from
    ``CONFIG.mt_demo_user_id`` (NFR2). Accepts an optional ``user_id``
    override for parity with ``check_payment_options``.
    """
    user_id = str(args.get("user_id") or CONFIG.mt_demo_user_id)
    include = "class_session,spot"
    # MT sandbox uses ``status="pending"`` for active bookings, so a
    # server-side ``filter[status]=confirmed`` returns zero hits; we
    # pull the full user list and let the client-side filter below
    # drop explicit cancels / waitlist entries instead.
    try:
        resp = await _mt_get(
            "/reservations/",
            {"filter[user]": user_id, "include": include},
        )
    except MTHttpError:
        return {"reservations": [], "count": 0, "error": "lookup_failed"}

    data = resp.get("data") or []
    included = resp.get("included") or []

    session_by_id: dict[str, dict] = {}
    spot_by_id: dict[str, dict] = {}
    for entry in included:
        etype = entry.get("type") or ""
        eid = str(entry.get("id") or "")
        if not eid:
            continue
        if etype == "class_sessions":
            session_by_id[eid] = entry
        elif etype == "spots":
            spot_by_id[eid] = entry

    now_utc = datetime.now(timezone.utc)
    seen: set[str] = set()
    rows: list[tuple[datetime, dict]] = []
    for r in data:
        rid = str(r.get("id") or "").strip()
        if not rid or rid in seen:
            continue
        attrs = r.get("attributes") or {}
        # MT uses ``cancel_date`` (non-null when canceled) as the canonical
        # cancellation signal; ``status`` is an orthogonal lifecycle field
        # where ``pending`` covers the bulk of active bookings in the
        # sandbox. Treat anything without a ``cancel_date`` as a live
        # reservation, and still explicitly drop waitlist-style statuses
        # so we don't apologize with a waitlisted slot as though it were
        # confirmed.
        if attrs.get("cancel_date"):
            continue
        status = str(attrs.get("status") or "").strip().lower()
        if status in ("canceled", "cancelled", "waitlist", "waitlisted"):
            continue

        rels = r.get("relationships") or {}
        cs_data = (rels.get("class_session") or {}).get("data") or {}
        cs_id = str(cs_data.get("id") or "")
        cs = session_by_id.get(cs_id, {})
        cs_attrs = cs.get("attributes") or {}

        start_dt = _parse_start_datetime(cs_attrs)
        if start_dt is None:
            continue
        # Normalize to UTC for the ordering / past-drop comparison.
        start_utc = start_dt.astimezone(timezone.utc)
        if start_utc < now_utc:
            continue

        class_name = str(cs_attrs.get("class_type_display") or "Class")
        instructors = cs_attrs.get("instructor_names") or []
        if instructors:
            class_name = f"{class_name} w/ {instructors[0]}"
        start_time_local = _format_session_time(cs_attrs)

        spot_rel = (rels.get("spot") or {}).get("data") or {}
        spot_id = str(spot_rel.get("id") or "")
        spot_label = ""
        if spot_id and spot_id in spot_by_id:
            s_attrs = spot_by_id[spot_id].get("attributes") or {}
            name = str(s_attrs.get("name") or "").strip()
            if name:
                spot_label = name
            else:
                spot_number = s_attrs.get("spot_number")
                if spot_number is not None and str(spot_number).strip():
                    spot_label = str(spot_number).strip()

        seen.add(rid)
        rows.append(
            (
                start_utc,
                {
                    "id": rid,
                    "class_name": class_name,
                    "start_time_local": start_time_local,
                    "spot_label": spot_label,
                },
            )
        )

    rows.sort(key=lambda t: t[0])
    reservations = [row for _, row in rows]
    return {"reservations": reservations, "count": len(reservations)}


def _map_cancel_error(err: MTHttpError) -> tuple[str, str]:
    """Map an :class:`MTHttpError` from the cancel endpoint to a
    compact ``(error_code, short_message)`` pair the model narrates.

    Codes (AC4): ``already_canceled`` / ``not_owner`` / ``past_class``
    / ``lookup_failed``. Messages kept under ~80 chars — spoken
    verbatim in the apology sentence, so no jargon.
    """
    status = err.status
    body_lc = (err.body_head or "").lower()

    if status == 409 or "already" in body_lc or "cancel" in body_lc:
        # ``cancel`` lands here because MT's "already canceled" error
        # messages typically say "Reservation is already canceled."
        return "already_canceled", "that one's already canceled"
    if status in (401, 403) or "permission" in body_lc or "owner" in body_lc:
        return "not_owner", "I can't cancel that one for this account"
    if "past" in body_lc or "ended" in body_lc or "started" in body_lc:
        return "past_class", "that class has already started"
    return "lookup_failed", "I couldn't cancel that one"


async def execute_cancel_reservation(args: dict) -> dict:
    """Story 2.2 modify-execution (AC3, AC4).

    Cancels one reservation by id. Probes
    ``POST /reservations/{id}/cancel`` with an empty JSON:API body
    first; on 404 / 405 / 409 (some sandboxes reject the action path
    or report "already handled"), falls back to
    ``PATCH /reservations/{id}`` with
    ``attributes.status = "canceled"``. Whichever variant returns 2xx
    is reported as success. Non-2xx from both variants routes through
    :func:`_map_cancel_error` and returns
    ``{success: false, error, message}``.

    ``class_name`` / ``start_time_local`` in the success payload are
    echo fields (MT's cancel response typically omits them) — the
    model passes them in from the prior ``list_user_reservations``
    narration; the handler also backfills from
    ``_last_listed_reservations`` if the model omits them (see
    handler.py).
    """
    reservation_id = str(args.get("reservation_id") or "").strip()
    if not reservation_id:
        return {
            "success": False,
            "error": "lookup_failed",
            "message": "missing reservation_id",
        }

    action_body = {
        "data": {
            "type": "reservations",
            "id": reservation_id,
            "attributes": {},
        }
    }
    try:
        # MT requires the trailing slash on action endpoints; without it
        # the sandbox 404s rather than following the implicit redirect.
        await _mt_post(
            f"/reservations/{reservation_id}/cancel/", action_body
        )
    except MTHttpError as err:
        # Fall back to PATCH when the sandbox doesn't expose the
        # action endpoint. 404/405 = wrong path/method; 409 sometimes
        # indicates "use PATCH instead" on quirky installs — probe
        # once and let the PATCH error (if any) be the final word.
        if err.status in (404, 405) or err.status == 0:
            patch_body = {
                "data": {
                    "type": "reservations",
                    "id": reservation_id,
                    "attributes": {"status": "canceled"},
                }
            }
            try:
                await _mt_patch(
                    f"/reservations/{reservation_id}", patch_body
                )
            except MTHttpError as err2:
                code, msg = _map_cancel_error(err2)
                return {
                    "success": False,
                    "error": code,
                    "message": msg,
                }
        else:
            code, msg = _map_cancel_error(err)
            return {"success": False, "error": code, "message": msg}

    return {
        "success": True,
        "reservation_id": reservation_id,
        "class_name": str(args.get("class_name") or ""),
        "start_time_local": str(args.get("start_time_local") or ""),
    }


# ---------------------------------------------------------------------------
# Story 2.7 — studio FAQ executor
# ---------------------------------------------------------------------------


async def execute_end_call(args: dict) -> dict:
    """Hang-up signal from the LLM.

    The executor itself does not terminate the session — it returns a
    marker dict that the handler reads in ``_dispatch_tool_calls`` and
    promotes to a ``StopAction(closing_speech=..., voice=...)`` yield
    on the same turn. Keeping the termination in the handler (rather
    than this async function) preserves the handler's existing yield
    contract: only event handlers emit events, tools just return data.

    ``closing_speech`` is the ONE short sentence the runtime will
    speak immediately before disconnecting the call. Empty / missing
    → the handler falls back to its canonical ``GOODBYE`` constant so
    the caller always hears a friendly sign-off.
    """
    closing = str(args.get("closing_speech") or "").strip()
    return {"success": True, "closing_speech": closing}


async def execute_lookup_studio_info(args: dict) -> dict:
    """Story 2.7 FAQ lookup (AC2, AC3).

    Returns ``{"topic": str, "answer": str}`` on hit, or
    ``{"topic": str, "answer": "", "error": <code>}`` otherwise. Codes:

    * ``"knowledge_unavailable"`` — import-time load of
      ``studio_knowledge.json`` failed (empty module-level dict); the
      tool still answers so the handler can narrate the honest fallback.
    * ``"unknown_topic"`` — the requested topic is not in the JSON
      (including empty / missing ``topic`` arg).

    Topic matching is case-insensitive; the normalized lowercase key is
    echoed back in the result so the model's narration can refer to it.
    Pure in-memory dict lookup — no network, no file I/O per call
    (AC11 / NFR1).
    """
    topic = str(args.get("topic") or "").strip().lower()

    if not _STUDIO_KNOWLEDGE:
        return {
            "topic": topic,
            "answer": "",
            "error": "knowledge_unavailable",
        }

    answer = _STUDIO_KNOWLEDGE.get(topic)
    if not answer:
        return {"topic": topic, "answer": "", "error": "unknown_topic"}
    return {"topic": topic, "answer": answer}


# ---------------------------------------------------------------------------
# ToolDefinitions — LLM-visible schema (AC1, AC2, AC3)
# ---------------------------------------------------------------------------

CHECK_PAYMENT_OPTIONS_TOOL = ToolDefinition(
    type="function",
    function=FunctionDefinition(
        name="check_payment_options",
        description=(
            "Use this BEFORE narrating classes to confirm the caller has "
            "active credits, a valid membership, or another bookable "
            "payment option. Returns {eligible, credits_remaining, "
            "reason}. If eligible is false, do NOT call "
            "list_class_sessions — apologize briefly and offer a "
            "callback. If eligible is true, you may then call "
            "list_class_sessions."
        ),
        parameters={
            "type": "object",
            "properties": {
                "user_id": {
                    "type": "string",
                    "description": (
                        "Optional caller user id. Omit in the hackathon "
                        "demo — the tool uses the configured demo user."
                    ),
                }
            },
            "required": [],
        },
    ),
)

LIST_CLASS_SESSIONS_TOOL = ToolDefinition(
    type="function",
    function=FunctionDefinition(
        name="list_class_sessions",
        description=(
            "Return the next 2–3 upcoming public class sessions. "
            "ONLY call this after check_payment_options returned "
            "eligible=true in this session. Returns {sessions:[{id, "
            "name, start_time_local, location}], count}. Use the "
            "returned start_time_local strings verbatim when speaking "
            "times to the caller, and remember each session id so a "
            "follow-up utterance can be matched back."
        ),
        parameters={
            "type": "object",
            "properties": {
                "min_date": {
                    "type": "string",
                    "description": (
                        "ISO date (YYYY-MM-DD). Defaults to today."
                    ),
                },
                "max_date": {
                    "type": "string",
                    "description": (
                        "ISO date (YYYY-MM-DD). Defaults to today + 7 days."
                    ),
                },
            },
            "required": [],
        },
    ),
)


DISCOVER_SPOTS_TOOL = ToolDefinition(
    type="function",
    function=FunctionDefinition(
        name="discover_spots",
        description=(
            "Call this AFTER the caller says a spot by name/number "
            "(e.g. 'Bike 5', 'the corner bike') and BEFORE "
            "create_reservation. Returns the classroom layout for the "
            "chosen class — the list of spots with their spoken labels "
            "and availability — so you can map the caller's utterance "
            "to a concrete spot_id. Shape: {spots:[{id, label, "
            "is_available, neighbor_ids}], count}. If the requested "
            "spot's is_available is false, apologize and offer one "
            "adjacent spot from neighbor_ids only — never a guess."
        ),
        parameters={
            "type": "object",
            "properties": {
                "class_session_id": {
                    "type": "string",
                    "description": (
                        "Required. The id of the class_session the "
                        "caller chose, taken from the "
                        "list_class_sessions result you narrated."
                    ),
                }
            },
            "required": ["class_session_id"],
        },
    ),
)


CREATE_RESERVATION_TOOL = ToolDefinition(
    type="function",
    function=FunctionDefinition(
        name="create_reservation",
        description=(
            "Call this ONLY after the caller verbally confirms a "
            "specific class you already narrated. Takes the chosen "
            "session's id and creates a standard reservation for the "
            "demo user. Returns {success, reservation_id, class_name, "
            "start_time_local} on success, or {success:false, error, "
            "message} on failure (error ∈ spot_unavailable, "
            "class_full, not_eligible, lookup_failed). On failure, "
            "apologize in ONE short sentence and keep the line open — "
            "do not hang up."
        ),
        parameters={
            "type": "object",
            "properties": {
                "class_session_id": {
                    "type": "string",
                    "description": (
                        "Required. The id of the class_session the "
                        "caller just confirmed, taken from the "
                        "list_class_sessions result you narrated."
                    ),
                },
                "spot_id": {
                    "type": "string",
                    "description": (
                        "Optional — pass the id returned by "
                        "discover_spots when the caller named a "
                        "specific spot (e.g. 'Bike 5'). Omit if the "
                        "caller did not name a spot; MT will "
                        "auto-assign."
                    ),
                },
                "class_name": {
                    "type": "string",
                    "description": (
                        "Optional — pass the session's display name "
                        "so the confirmation reads naturally (e.g. "
                        "'Ride w/ Taylor')."
                    ),
                },
                "start_time_local": {
                    "type": "string",
                    "description": (
                        "Optional — pass the session's "
                        "start_time_local string so the confirmation "
                        "reads naturally (e.g. 'Sat 6:00 PM')."
                    ),
                },
            },
            "required": ["class_session_id"],
        },
    ),
)


LIST_USER_RESERVATIONS_TOOL = ToolDefinition(
    type="function",
    function=FunctionDefinition(
        name="list_user_reservations",
        description=(
            "Call this when the caller wants to cancel or reschedule "
            "something (e.g. 'cancel my 6 PM', 'move my 6 to the 7'). "
            "Returns the demo user's upcoming reservations so you can "
            "pick the one they mean. Returns only upcoming, non-"
            "canceled reservations. Shape: {reservations:[{id, "
            "class_name, start_time_local, spot_label}], count}. Use "
            "the returned start_time_local strings verbatim when "
            "reading matches back to the caller."
        ),
        parameters={
            "type": "object",
            "properties": {
                "user_id": {
                    "type": "string",
                    "description": (
                        "Optional caller user id. Omit in the "
                        "hackathon demo — the tool uses the "
                        "configured demo user."
                    ),
                }
            },
            "required": [],
        },
    ),
)


CANCEL_RESERVATION_TOOL = ToolDefinition(
    type="function",
    function=FunctionDefinition(
        name="cancel_reservation",
        description=(
            "Call this AFTER the caller firmly confirms which "
            "reservation to cancel (use list_user_reservations first "
            "to resolve ambiguity if the caller referred to the class "
            "by time). Cancels exactly one reservation by id. For a "
            "reschedule, call cancel_reservation first, then call "
            "create_reservation for the new class — both sides must "
            "succeed or you must narrate the partial state. Returns "
            "{success, reservation_id, class_name, start_time_local} "
            "on success, or {success:false, error, message} on "
            "failure (error ∈ already_canceled, not_owner, "
            "past_class, lookup_failed)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "reservation_id": {
                    "type": "string",
                    "description": (
                        "Required. The id of the reservation the "
                        "caller just confirmed, taken from the "
                        "list_user_reservations result you narrated."
                    ),
                },
                "class_name": {
                    "type": "string",
                    "description": (
                        "Optional — pass the canceled session's "
                        "display name (from list_user_reservations) "
                        "so the confirmation reads naturally."
                    ),
                },
                "start_time_local": {
                    "type": "string",
                    "description": (
                        "Optional — pass the canceled session's "
                        "start_time_local string (from "
                        "list_user_reservations) so the confirmation "
                        "reads naturally."
                    ),
                },
            },
            "required": ["reservation_id"],
        },
    ),
)


LOOKUP_STUDIO_INFO_TOOL = ToolDefinition(
    type="function",
    function=FunctionDefinition(
        name="lookup_studio_info",
        description=(
            "Call this when the caller asks a studio-information "
            "question — hours, pricing, amenities, location, or class "
            "types. Returns the pre-authored answer for that topic. "
            "Do not use for booking, cancellation, or class "
            "availability — those have their own tools. Returns "
            "{topic, answer} on success, or {topic, answer:'', "
            "error} on miss (error ∈ unknown_topic, "
            "knowledge_unavailable). On error, admit the gap in ONE "
            "sentence and offer a booking instead — never invent."
        ),
        parameters={
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "enum": list(FAQ_TOPICS),
                    "description": (
                        "Required. The studio-info topic the caller "
                        "asked about. Pick the closest match from the "
                        "enum; if nothing fits, call anyway and the "
                        "tool will return unknown_topic."
                    ),
                }
            },
            "required": ["topic"],
        },
    ),
)


END_CALL_TOOL = ToolDefinition(
    type="function",
    function=FunctionDefinition(
        name="end_call",
        description=(
            "Call this to hang up when the caller has signaled they're "
            "done — e.g. after a successful booking, cancel, "
            "reschedule, or FAQ answer they say 'no thanks', 'that's "
            "all', 'I'm good', 'bye', 'we're done', or otherwise "
            "indicate nothing else is needed. Do NOT call mid-flow "
            "(while still gathering info, waiting on a booking "
            "confirmation, or answering an open question). Do NOT "
            "call if the caller asks another question in the same "
            "utterance. The runtime speaks your closing_speech and "
            "then disconnects — this is a one-way action."
        ),
        parameters={
            "type": "object",
            "properties": {
                "closing_speech": {
                    "type": "string",
                    "description": (
                        "Required. ONE short, on-brand sentence to "
                        "speak right before hanging up (e.g. 'Thanks "
                        "for calling Barry's Chelsea — see you in "
                        "the Red Room!'). Coach-like and confident, "
                        "no markdown, no lists."
                    ),
                }
            },
            "required": ["closing_speech"],
        },
    ),
)


TOOLS: list[ToolDefinition] = [
    CHECK_PAYMENT_OPTIONS_TOOL,
    LIST_CLASS_SESSIONS_TOOL,
    DISCOVER_SPOTS_TOOL,
    CREATE_RESERVATION_TOOL,
    LIST_USER_RESERVATIONS_TOOL,
    CANCEL_RESERVATION_TOOL,
    LOOKUP_STUDIO_INFO_TOOL,
    END_CALL_TOOL,
]


ToolExecutor = Callable[[dict], Awaitable[dict]]

TOOL_DISPATCH: dict[str, ToolExecutor] = {
    "check_payment_options": execute_check_payment_options,
    "list_class_sessions": execute_list_class_sessions,
    "discover_spots": execute_discover_spots,
    "create_reservation": execute_create_reservation,
    "list_user_reservations": execute_list_user_reservations,
    "cancel_reservation": execute_cancel_reservation,
    "lookup_studio_info": execute_lookup_studio_info,
    "end_call": execute_end_call,
}
