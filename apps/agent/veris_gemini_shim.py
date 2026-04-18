"""Story 2.6 — Gemini direct-call shim for the Veris sandbox.

Why this exists
---------------

``primfunctions.completions`` is a **proxy-only** library. Every call to
``generate_chat_completion[_stream]`` funnels through a VoiceRun-managed
HTTP gateway (see ``primfunctions/completions/client.py:152`` — "intra-
cluster HTTP, no TLS, Authorization is per-request"). Inside the Veris
AI sandbox that gateway is unreachable, so ``configure_provider`` either
raises ``CompletionsNotConfiguredError`` or the subsequent ``POST``
hits a dead URL and the handler crashes before emitting any events —
which is exactly what Milestone A's first run showed (sims failed with
no logs, Dev-Notes P1 risk realised).

This module replaces the relevant primfunctions symbols in-place with a
minimal stdlib-only Gemini REST client. The shim runs **before**
``veris_adapter`` imports ``handler``, so the handler's
``from primfunctions.completions import generate_chat_completion``
statement picks up our function instead of the original proxy-bound one.

Covered by Story 2.6 AC5/AC7/AC9/AC14/AC16:

* Stdlib only — no aiohttp / google-generativeai. Keeps
  ``requirements.txt`` pinned to ``primfunctions`` (AC14).
* ``handler.py`` is NOT modified (AC16). The handler's
  ``configure_provider(..., voicerun_managed=True)`` call is a no-op
  after ``install()`` runs.
* Errors surface through the adapter's ``agent_error`` stderr log
  (AC9) — Gemini HTTP 4xx/5xx rolls up as a ``RuntimeError`` with the
  body attached.

Env contract
------------

* ``GEMINI_API_KEY`` — required. Set via ``veris env vars set
  GEMINI_API_KEY=... --secret`` so it never lands in a committed file
  or an image layer (AC18).

Known limitations
-----------------

* Streaming is emulated: the shim runs one non-streaming Gemini call
  and splits the text into sentences to feed the handler's
  ``stream_sentences: True`` consumer. Veris scores the concatenated
  reply, not TTS granularity, so this is behaviourally equivalent for
  evaluation purposes.
* The shim mints tool-call ids (``call_<hex>``) because Gemini does not
  return per-call ids. The handler only round-trips them through
  ``ToolResultMessage.tool_call_id`` → Gemini's ``functionResponse.name``
  mapping, which we rebuild from ``ToolResultMessage.name``.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import urllib.error
import urllib.request
import uuid
from typing import Any, AsyncIterator

import primfunctions.completions as _pfc
from primfunctions.completions.types import (
    AssistantMessage,
    ChatCompletionResponse,
    ContentSentenceChunk,
    FinalResponseChunk,
    FunctionCall,
    ToolCall,
)

# Gemini v1beta REST endpoint. v1beta carries the function-calling
# contract (v1 stable does not at time of writing).
_GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

# Conservative sentence splitter — handler's stream_options
# ``stream_sentences: True`` expects complete phrases on each chunk, not
# token-level deltas. English-only is fine for the Chelsea demo.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'“‘])")

# Schema keywords Gemini's functionDeclarations rejects that OpenAI/
# JSON-Schema tooling routinely emits. Stripped recursively before the
# request is sent; none of them carry semantics Gemini can enforce.
_SCHEMA_DROP_KEYS = frozenset(
    {
        "additionalProperties",
        "$schema",
        "$defs",
        "definitions",
        "$id",
        "$ref",
    }
)


def _api_key() -> str:
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not key:
        raise RuntimeError(
            "GEMINI_API_KEY not set. Run `veris env vars set "
            "GEMINI_API_KEY=... --secret` so the Veris sandbox can reach "
            "Gemini directly (primfunctions' proxy is cluster-internal "
            "and unreachable from the sandbox)."
        )
    return key


def _is_dict(x: Any) -> bool:
    return isinstance(x, dict)


def _attr_or_key(obj: Any, name: str, default: Any = None) -> Any:
    if _is_dict(obj):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _sentences(text: str) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    parts = _SENTENCE_SPLIT.split(text)
    return [p.strip() for p in parts if p.strip()]


def _sanitize_schema(schema: Any) -> Any:
    if isinstance(schema, dict):
        return {
            k: _sanitize_schema(v)
            for k, v in schema.items()
            if k not in _SCHEMA_DROP_KEYS
        }
    if isinstance(schema, list):
        return [_sanitize_schema(x) for x in schema]
    return schema


def _tools_to_gemini(tools: list[Any] | None) -> list[dict]:
    if not tools:
        return []
    decls: list[dict] = []
    for t in tools:
        fn = _attr_or_key(t, "function")
        if fn is None:
            continue
        name = _attr_or_key(fn, "name", "")
        desc = _attr_or_key(fn, "description", "")
        params = _attr_or_key(fn, "parameters", {}) or {}
        decls.append(
            {
                "name": name,
                "description": desc,
                "parameters": _sanitize_schema(params),
            }
        )
    return [{"functionDeclarations": decls}] if decls else []


def _args_to_dict(args: Any) -> dict:
    if isinstance(args, dict):
        return args
    if isinstance(args, str):
        try:
            parsed = json.loads(args)
            return parsed if isinstance(parsed, dict) else {"value": parsed}
        except Exception:
            return {"raw_arguments": args}
    return {}


def _messages_to_gemini(
    messages: list[Any],
) -> tuple[list[dict], list[dict]]:
    """Convert primfunctions message list → (systemInstruction parts,
    contents). Gemini separates the system turn from the conversation
    body, unlike OpenAI which inlines it.
    """
    system_parts: list[dict] = []
    contents: list[dict] = []

    for m in messages:
        role = _attr_or_key(m, "role")
        if role == "system":
            text = _attr_or_key(m, "content", "")
            system_parts.append({"text": str(text)})
            continue

        if role == "user":
            text = _attr_or_key(m, "content", "")
            contents.append(
                {"role": "user", "parts": [{"text": str(text)}]}
            )
            continue

        if role == "assistant":
            parts: list[dict] = []
            text = _attr_or_key(m, "content")
            if text:
                parts.append({"text": str(text)})
            for tc in _attr_or_key(m, "tool_calls", []) or []:
                fn = _attr_or_key(tc, "function")
                if fn is None:
                    continue
                parts.append(
                    {
                        "functionCall": {
                            "name": _attr_or_key(fn, "name", ""),
                            "args": _args_to_dict(
                                _attr_or_key(fn, "arguments", {})
                            ),
                        }
                    }
                )
            if parts:
                contents.append({"role": "model", "parts": parts})
            continue

        if role == "tool":
            # ``handler.py`` always populates ``name`` on
            # ``ToolResultMessage`` (line 864-868), so we can rely on it
            # to anchor Gemini's ``functionResponse.name`` contract.
            name = _attr_or_key(m, "name") or _attr_or_key(
                m, "tool_call_id"
            ) or "tool"
            content = _attr_or_key(m, "content", {})
            response_obj = (
                content if isinstance(content, dict) else {"result": content}
            )
            contents.append(
                {
                    "role": "user",
                    "parts": [
                        {
                            "functionResponse": {
                                "name": str(name),
                                "response": response_obj,
                            }
                        }
                    ],
                }
            )
            continue

    return system_parts, contents


def _request_to_dict(request: Any) -> dict:
    if isinstance(request, dict):
        return request
    return {
        "provider": getattr(request, "provider", "google"),
        "model": getattr(request, "model", None),
        "messages": list(getattr(request, "messages", []) or []),
        "temperature": getattr(request, "temperature", None),
        "max_tokens": getattr(request, "max_tokens", None),
        "tools": getattr(request, "tools", None),
        "tool_choice": getattr(request, "tool_choice", None),
    }


async def _post_gemini(model: str, body: dict) -> dict:
    url = f"{_GEMINI_BASE}/{model}:generateContent?key={_api_key()}"
    data = json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json"}

    def _blocking() -> dict:
        req = urllib.request.Request(
            url, data=data, headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                detail = exc.read().decode("utf-8", errors="replace")[:800]
            except Exception:
                detail = ""
            raise RuntimeError(
                f"Gemini HTTP {exc.code}: {detail}"
            ) from exc

    return await asyncio.to_thread(_blocking)


def _unpack_candidate(raw: dict) -> tuple[str, list[ToolCall], str]:
    candidates = raw.get("candidates") or []
    if not candidates:
        return "", [], "stop"
    cand = candidates[0]
    parts = (cand.get("content") or {}).get("parts") or []
    text_parts: list[str] = []
    tool_calls: list[ToolCall] = []
    for part in parts:
        if "text" in part and part["text"]:
            text_parts.append(part["text"])
        elif "functionCall" in part:
            fc = part["functionCall"]
            tool_calls.append(
                ToolCall(
                    id="call_" + uuid.uuid4().hex[:12],
                    type="function",
                    function=FunctionCall(
                        name=fc.get("name", ""),
                        arguments=fc.get("args") or {},
                    ),
                )
            )
    finish = cand.get("finishReason", "STOP") or "STOP"
    return (
        " ".join(text_parts).strip(),
        tool_calls,
        str(finish).lower(),
    )


async def generate_chat_completion(request: Any) -> ChatCompletionResponse:
    """Drop-in replacement for ``primfunctions.completions.generate_chat_completion``.

    Routes one non-streaming Gemini REST call. Returns a populated
    ``ChatCompletionResponse`` so the handler's tool-dispatch loop and
    ``messages.append(response.message)`` flow works unchanged.
    """
    req_d = _request_to_dict(request)
    model = req_d.get("model") or "gemini-2.5-flash"
    system_parts, contents = _messages_to_gemini(req_d.get("messages") or [])

    # ``thinkingBudget: 0`` disables Gemini 2.5 Flash's internal
    # reasoning tokens. Those tokens count against ``maxOutputTokens``
    # but produce no visible output, so with handler.py's conservative
    # budgets (200 first turn, 140 streaming) thinking can starve the
    # reply. Turning it off keeps Flash behaviour close to Gemini 1.5's
    # for the booking-flow scenarios Story 2.1 tuned against.
    body: dict[str, Any] = {
        "contents": contents,
        "generationConfig": {"thinkingConfig": {"thinkingBudget": 0}},
    }
    if system_parts:
        body["systemInstruction"] = {"parts": system_parts}
    if req_d.get("temperature") is not None:
        body["generationConfig"]["temperature"] = req_d["temperature"]
    if req_d.get("max_tokens") is not None:
        body["generationConfig"]["maxOutputTokens"] = req_d["max_tokens"]
    tools = _tools_to_gemini(req_d.get("tools"))
    if tools:
        body["tools"] = tools
        body["toolConfig"] = {"functionCallingConfig": {"mode": "AUTO"}}

    raw = await _post_gemini(model, body)
    text, tool_calls, finish = _unpack_candidate(raw)

    return ChatCompletionResponse(
        message=AssistantMessage(
            content=text or None,
            tool_calls=tool_calls or None,
        ),
        finish_reason=finish,
        usage=raw.get("usageMetadata"),
        provider="google",
        model=model,
    )


async def generate_chat_completion_stream(
    request: Any,
    stream_options: Any = None,
) -> AsyncIterator[Any]:
    """Streaming wrapper.

    Fires the non-streaming call, sentence-splits the result, and yields
    ``ContentSentenceChunk`` then ``FinalResponseChunk``. The handler's
    streaming consumer (``handler.py:1098``) only inspects
    ``chunk.type in ('content_sentence', 'response')``, so this is
    behaviourally equivalent for evaluation; TTS granularity differs but
    Veris scores the concatenated reply.
    """
    response = await generate_chat_completion(request)
    text = response.message.content or ""

    async def _gen() -> AsyncIterator[Any]:
        for sentence in _sentences(text):
            yield ContentSentenceChunk(sentence=sentence)
        yield FinalResponseChunk(response=response)

    return _gen()


def _noop(*_args: Any, **_kwargs: Any) -> None:
    """Replacement for ``configure`` / ``configure_provider``. The
    handler calls these at turn-0 to register the VoiceRun-managed
    proxy session; under Veris we don't use the proxy so the calls are
    pure side-effects we want to skip.
    """
    return None


def install() -> None:
    """Replace the primfunctions completion symbols in-place.

    Idempotent — subsequent calls are a no-op. Must run before
    ``from handler import handler`` inside ``veris_adapter`` so the
    handler's ``from primfunctions.completions import ...`` statements
    pick up the shim.
    """
    if getattr(_pfc, "_VERIS_SHIM_INSTALLED", False):
        return

    _pfc.configure = _noop
    _pfc.configure_provider = _noop
    _pfc.generate_chat_completion = generate_chat_completion
    _pfc.generate_chat_completion_stream = generate_chat_completion_stream

    # The actual function bodies live in ``primfunctions.completions.client``.
    # Anything else in the SDK that imports from there (e.g.
    # ``_warm_request``, which ``configure_provider`` fires-and-forgets)
    # needs the same treatment or it'll still try to hit the proxy.
    try:
        import primfunctions.completions.client as _client

        _client.configure = _noop
        _client.configure_provider = _noop
        _client.generate_chat_completion = generate_chat_completion
        _client.generate_chat_completion_stream = generate_chat_completion_stream
    except Exception:  # pragma: no cover — defensive
        pass

    _pfc._VERIS_SHIM_INSTALLED = True


install()
