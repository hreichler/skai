"""Story 2.6 — Veris AI Reliability Harness adapter.

See ``_bmad-output/implementation-artifacts/2-6-veris-ai-reliability-
harness.md#AC5``.

Purpose: expose the agent's reasoning loop (``handler.py`` async
generator) over a stdlib HTTP server so Veris AI can drive the SAME
generator used by the live VoiceRun runtime. Findings transfer 1:1 to
live-call behavior.

Design constraints (AC5 / AC6 / AC8 / AC9 / AC16):

* **Stdlib only** — ``http.server.ThreadingHTTPServer`` +
  ``asyncio`` + ``json``. No ``fastapi`` / ``flask`` / ``uvicorn``
  (requirements.txt stays at one line: ``primfunctions``).
* **Generator re-use** — every turn flows through the same
  ``handler(event, context)`` async generator that the live runtime
  drives. No parallel reasoning loop.
* **Per-session ``VerisContext``** kept in a process-local dict
  (fresh container per Veris simulation). Never persisted to disk.
* **DebugEvents logged to stderr** (Veris transcripts capture stderr)
  as JSON lines — not mixed into the ``response`` body.
* **``MT_*`` secrets** come from the process env (populated by
  ``veris env vars set --secret`` per AC4); never hardcoded here.
* **``handler.py`` is NOT modified** by this story (AC16). Any bug
  surfaced by Veris runs is filed as a separate patch so the first
  report measures the agent AS Story 2.1 left it.
"""

from __future__ import annotations

import asyncio
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

# When the container uses ``WORKDIR /app`` but the agent code lives at
# ``/agent/`` (AC7 Dockerfile.sandbox), ``python -m veris_adapter`` only
# resolves if ``/agent/`` is on ``sys.path``. Inserting the file's own
# directory covers both the container path and a local ``python
# veris_adapter.py`` smoke test from ``apps/agent/``.
_THIS_DIR = str(Path(__file__).resolve().parent)
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

# Install the Gemini direct-call shim BEFORE importing handler, so the
# handler's ``from primfunctions.completions import
# generate_chat_completion, generate_chat_completion_stream`` picks up
# our stdlib Gemini client instead of the VoiceRun-proxy functions. See
# ``veris_gemini_shim.py`` for the why (primfunctions is proxy-only, and
# the proxy is unreachable from the Veris sandbox — Dev-Notes P1 risk
# realised during the first Milestone-A push).
import veris_gemini_shim  # noqa: F401 — side-effect import

from primfunctions.events import (
    DebugEvent,
    StopEvent,
    TextEvent,
    TextToSpeechEvent,
)

from config import bootstrap_config
from handler import handler

# Mirror handler.py's alias — ``primfunctions.actions`` does not exist
# in runtime 1.x; ``StopEvent`` doubles as the hangup action.
StopAction = StopEvent

HOST = "0.0.0.0"
PORT = 8008

# Appended to the final turn's response string when the handler yields
# a StopEvent (AC5) so Veris actors see the hangup explicitly instead
# of inferring it from a dangling ``response``.
SESSION_ENDED_MARKER = "[session ended]"

# Reply returned when the handler generator raises. HTTP 200 on purpose:
# Veris scores agent failures, not server failures (AC9). A 500 would
# make every handler bug look like infrastructure, burying the real
# signal we're trying to measure.
AGENT_ERROR_REPLY = "Sorry — something went sideways on my end. Try again?"


class VerisContext:
    """Minimal shim of ``primfunctions.context.Context``'s handler-
    facing surface.

    Audit of ``apps/agent/handler.py`` (Stories 1.2 → 2.1) shows the
    only members the handler touches are:

    * ``get_data(key, default=None)`` / ``set_data(key, value)`` —
      session-scoped scratchpad
    * ``get_completion_messages()`` / ``set_completion_messages(list)``
      — LLM conversation history
    * ``variables`` attribute — read by
      ``config._context_var`` / ``config.bootstrap_config``
    * ``session_id`` attribute — read by
      ``handler._booking_progress_event`` for the event context
      payload

    Shim matches primfunctions.context.Context's handler-facing
    surface as of runtime 1.x. Re-verify on SDK bump. Any
    ``AttributeError`` raised during a Veris run is a Context-surface
    regression — log it and add the missing method; do NOT swallow.
    """

    __slots__ = ("_data", "_messages", "variables", "session_id")

    def __init__(self, session_id: str) -> None:
        self._data: dict[str, Any] = {}
        self._messages: list = []
        # Empty dict so ``bootstrap_config`` falls back to process env
        # — MT_* come from Veris's runtime-injected vars per AC4, not
        # from ``context.variables`` (which in the real runtime is
        # sourced from ``vr create variable`` — irrelevant under
        # Veris).
        self.variables: dict = {}
        self.session_id: str = session_id

    def get_data(self, key: str, default: Any = None) -> Any:
        # Matches real Context semantics: returns the stored value
        # when present (even if falsy like ``None``) and ``default``
        # only when the key is absent. Story 1.3's ``eligible`` cache
        # relies on this distinction.
        return self._data.get(key, default)

    def set_data(self, key: str, value: Any) -> None:
        self._data[key] = value

    def get_completion_messages(self) -> list:
        return self._messages

    def set_completion_messages(self, messages: list) -> None:
        # Store a copy so later mutations of the caller's list don't
        # retroactively edit our history.
        self._messages = list(messages)

    def append_completion_message(self, msg: Any) -> None:
        # Mirrored for forward-compat. The Story 1.2-2.1 handler does
        # not call this, but the real Context exposes it and a future
        # handler patch might use it.
        self._messages.append(msg)


_SESSIONS: dict[str, VerisContext] = {}


def _get_or_create_session(session_id: str) -> VerisContext:
    ctx = _SESSIONS.get(session_id)
    if ctx is None:
        ctx = VerisContext(session_id=session_id)
        _SESSIONS[session_id] = ctx
    return ctx


def _log_debug_event(out: DebugEvent) -> None:
    """Emit one JSON-line stderr record per handler ``DebugEvent``.

    Veris transcripts capture stderr; this makes the agent's working
    memory (``booking_progress``, ``agent_error``, etc. per
    ``docs/schema.md``) visible in the simulation report without
    polluting the HTTP ``response`` body.
    """
    try:
        payload = {
            "event_name": getattr(out, "event_name", ""),
            "event_data": getattr(out, "event_data", {}),
        }
        print(json.dumps(payload, default=str), file=sys.stderr)
    except Exception as exc:  # pragma: no cover — defensive only
        print(
            f"[veris_adapter] failed to serialize DebugEvent: {exc}",
            file=sys.stderr,
        )


async def _drive_turn(context: VerisContext, text: str) -> tuple[str, bool]:
    """Run ONE handler turn for ``text`` under ``context``.

    Returns ``(response_text, stopped)`` where ``stopped`` is True if
    the handler yielded a ``StopEvent`` — in which case the response
    gets the ``SESSION_ENDED_MARKER`` appended so Veris actors see
    the hangup explicitly.

    ``TimeoutEvent`` is NOT fabricated here — the HTTP channel has no
    dead-air concept; Veris's ``MAX_TURNS`` is the equivalent
    adversarial-silence mechanism (AC5).
    """
    # Idempotent after the first call per-process — same contract as
    # handler.py line 705. Fills CONFIG from process env (populated by
    # ``veris env vars set --secret``, AC4).
    bootstrap_config(context)

    event = TextEvent(text=text)
    texts: list[str] = []
    stopped = False

    gen = handler(event, context)
    async for out in gen:
        if isinstance(out, TextToSpeechEvent):
            sentence = (getattr(out, "text", "") or "").strip()
            if sentence:
                texts.append(sentence)
        elif isinstance(out, DebugEvent):
            _log_debug_event(out)
        elif isinstance(out, StopAction):
            stopped = True
            break
        else:
            # Unknown yield types are logged (useful signal on SDK
            # bumps) but don't crash the turn.
            print(
                f"[veris_adapter] unhandled yield: {type(out).__name__}",
                file=sys.stderr,
            )

    response = " ".join(texts).strip()
    if stopped:
        response = f"{response} {SESSION_ENDED_MARKER}".strip()
    return response, stopped


async def _drive_turn_safe(
    context: VerisContext, text: str
) -> tuple[str, bool]:
    """Wrap ``_drive_turn`` in an ``agent_error`` fallback (AC9).

    Catches every exception the handler can raise, logs a schema-
    shaped stderr record, and returns a safe apology string so Veris
    continues the simulation instead of scoring a "server error".
    """
    try:
        return await _drive_turn(context, text)
    except Exception as exc:
        payload = {
            "event_name": "agent_error",
            "event_data": {
                "message": str(exc),
                "where": "veris_adapter._drive_turn",
                "recoverable": True,
            },
        }
        try:
            print(json.dumps(payload, default=str), file=sys.stderr)
        except Exception:  # pragma: no cover — defensive
            print(f"[veris_adapter] agent_error: {exc}", file=sys.stderr)
        return AGENT_ERROR_REPLY, False


class ChatHandler(BaseHTTPRequestHandler):
    """Single-endpoint HTTP handler: ``POST /chat`` + health ``GET /``."""

    server_version = "VerisAdapter/1.0"

    def _json(self, status: int, body: dict) -> None:
        data = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler API
        if self.path in ("/", "/health"):
            self._json(200, {"status": "ok"})
            return
        self._json(404, {"error": "not_found", "path": self.path})

    def do_POST(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler API
        if self.path != "/chat":
            self._json(404, {"error": "not_found", "path": self.path})
            return

        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length > 0 else b""
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else {}
        except json.JSONDecodeError as exc:
            self._json(
                400,
                {"error": "invalid_json", "detail": str(exc)},
            )
            return

        if not isinstance(payload, dict):
            self._json(400, {"error": "payload_not_object"})
            return

        message = str(payload.get("message") or "").strip()
        session_id = str(payload.get("session_id") or "").strip()
        if not session_id:
            self._json(400, {"error": "missing_session_id"})
            return

        context = _get_or_create_session(session_id)

        # Fresh event loop per request — simpler than a shared
        # background loop, and latency is not a Milestone-A gate.
        # ``asyncio.run`` also guarantees the loop is closed cleanly,
        # which matters when Veris opens many short sessions in a row.
        try:
            response_text, _stopped = asyncio.run(
                _drive_turn_safe(context, message)
            )
        except RuntimeError as exc:
            # Defence against accidentally running inside an already-
            # live loop (future-proofing if we migrate to a shared
            # background loop). Logged as agent_error + safe reply so
            # Veris continues the scenario.
            print(
                json.dumps(
                    {
                        "event_name": "agent_error",
                        "event_data": {
                            "message": str(exc),
                            "where": "veris_adapter.do_POST.asyncio.run",
                            "recoverable": True,
                        },
                    }
                ),
                file=sys.stderr,
            )
            response_text = AGENT_ERROR_REPLY

        self._json(200, {"response": response_text})

    # Tag stderr request logs so DebugEvent JSON lines don't get mixed
    # up with noisy access logs in the Veris transcript.
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        print(
            f"[veris_adapter.http] {self.address_string()} - "
            f"{format % args}",
            file=sys.stderr,
        )


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), ChatHandler)
    print(
        f"[veris_adapter] Listening on {HOST}:{PORT}",
        file=sys.stderr,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("[veris_adapter] shutting down (SIGINT)", file=sys.stderr)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
