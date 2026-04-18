"""Typed agent config — Story 1.0 (revised Story 1.3).

Two-tier contract (see AC7 in _bmad-output/implementation-artifacts/
1-0-voicerun-cli-polyglot-workspace-scaffold.md):

* Hard-required (handler raises on first use if missing):
    - MT_BASE_URL
    - MT_ACCESS_TOKEN
* Soft (readable, typed Optional[str]):
    - MT_CLIENT_ID
    - MT_CLIENT_SECRET
    - MT_DEMO_USER_ID   (Story 1.3 NFR2 — hardcoded Demo User id for the
                        payment_options gate; falls back to "34725" when
                        absent because that user is known-eligible in the
                        reseat sandbox. Flip to 35092 (or similar) for
                        ineligible-path verification.)
    - VERIS_AI_API_KEY
* NOT loaded here:
    - VOICERUN_API_KEY       (CI-only; local uses `vr signin` OAuth)
    - NEXT_PUBLIC_DASHBOARD_URL (dashboard-side, see apps/dashboard/lib/config.ts)

## Two-source hydration (Story 1.3 fix)

``.env.local`` is workspace-scoped and does NOT ship to the VoiceRun runtime
(only ``apps/agent/`` is packaged by ``vr push``). So for the deployed path we
read from ``context.variables`` — which is how the runtime exposes
org/environment variables registered via ``vr create variable``. Locally we
still honor ``.env.local`` + process env so ``python -c`` smoke tests and
direct handler invocations keep working without touching the CLI.

Import is NON-raising: ``CONFIG`` initializes with whatever ``os.environ`` /
``.env.local`` provide (possibly empty). The handler MUST call
``bootstrap_config(context)`` at the top of every event — it's idempotent
and fills any still-empty fields from ``context.variables``, then validates
the hard-required fields. If you hit ``CONFIG.mt_base_url`` without calling
``bootstrap_config`` first on the deployed runtime, the pre-existing
``_require``-style RuntimeError surfaces at use time (which is fine, it's
loud and pinpoints the missing var).

Stdlib-only on purpose — requirements.txt stays pinned to ``primfunctions``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    ENV_FILE: Path | None = Path(__file__).resolve().parents[2] / ".env.local"
except IndexError:
    # Running from a mount shallower than the skai workspace (e.g. the Veris
    # sandbox copies apps/agent into /agent/, so parents[2] doesn't exist).
    # No workspace-scoped .env.local to read; vars come from the runtime's
    # injected process env instead.
    ENV_FILE = None

DEFAULT_DEMO_USER_ID = "34725"

# Keys we try to hydrate from ``context.variables`` when the process env
# doesn't already supply them. Kept explicit (not a wildcard) so a rogue
# variable can never shadow something unexpected.
_CONTEXT_VARIABLE_KEYS = (
    "MT_BASE_URL",
    "MT_ACCESS_TOKEN",
    "MT_CLIENT_ID",
    "MT_CLIENT_SECRET",
    "MT_DEMO_USER_ID",
    "VERIS_AI_API_KEY",
)


def _load_env_file(path: Path | None) -> None:
    if path is None or not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), value)


_load_env_file(ENV_FILE)


@dataclass
class AgentConfig:
    """Mutable on purpose: ``bootstrap_config`` may fill fields after import
    once a runtime ``Context`` is available. Mutation is one-way (empty →
    populated); ``_bootstrapped`` guards against re-fetching every event.
    """

    mt_base_url: str = ""
    mt_access_token: str = ""
    mt_client_id: str | None = None
    mt_client_secret: str | None = None
    mt_demo_user_id: str = DEFAULT_DEMO_USER_ID
    veris_ai_api_key: str | None = None
    _bootstrapped: bool = field(default=False, repr=False)

    def require(self, name: str) -> str:
        """Loud getter for hard-required fields. Mirrors the Story 1.0
        ``_require`` error message so ``docs/architecture`` troubleshooting
        notes still apply.
        """
        value = getattr(self, name, "") or ""
        if not value:
            env_name = {
                "mt_base_url": "MT_BASE_URL",
                "mt_access_token": "MT_ACCESS_TOKEN",
            }.get(name, name.upper())
            raise RuntimeError(
                f"Missing required env var: {env_name} "
                "(expected in .env.local locally, or via "
                "`vr create variable --org` for the deployed runtime)"
            )
        return value


def _from_env() -> AgentConfig:
    return AgentConfig(
        mt_base_url=os.environ.get("MT_BASE_URL", "") or "",
        mt_access_token=os.environ.get("MT_ACCESS_TOKEN", "") or "",
        mt_client_id=os.environ.get("MT_CLIENT_ID") or None,
        mt_client_secret=os.environ.get("MT_CLIENT_SECRET") or None,
        mt_demo_user_id=os.environ.get("MT_DEMO_USER_ID") or DEFAULT_DEMO_USER_ID,
        veris_ai_api_key=os.environ.get("VERIS_AI_API_KEY") or None,
    )


CONFIG = _from_env()


def _context_var(context: Any, key: str) -> str | None:
    """Read ``key`` from ``context.variables`` across the two shapes we've
    seen in ``primfunctions`` — a ``dict`` and an object with ``.get``.
    Returns ``None`` if missing or on any attribute/type error.
    """
    variables = getattr(context, "variables", None)
    if variables is None:
        return None
    try:
        if hasattr(variables, "get"):
            val = variables.get(key)
        elif isinstance(variables, dict):
            val = variables.get(key)
        else:
            val = None
    except Exception:  # pragma: no cover — defensive only
        return None
    if val is None:
        return None
    val = str(val).strip()
    return val or None


def bootstrap_config(context: Any) -> AgentConfig:
    """Hydrate ``CONFIG`` from ``context.variables`` on first call.

    Idempotent — subsequent calls are a no-op. Designed to be called from
    the top of every event handler without measurable overhead.

    Precedence (highest first):
      1. Fields already populated (process env / ``.env.local``).
      2. ``context.variables[KEY]`` — deployed-runtime source of truth.
      3. Static defaults (e.g. ``MT_DEMO_USER_ID`` → ``"34725"``).
    """
    if CONFIG._bootstrapped:
        return CONFIG

    for key in _CONTEXT_VARIABLE_KEYS:
        # Map env-style key to dataclass attribute.
        attr = key.lower()
        current = getattr(CONFIG, attr, None)
        if current:  # already filled from env/.env.local
            continue
        value = _context_var(context, key)
        if value:
            setattr(CONFIG, attr, value)

    # Apply the demo-user fallback AFTER hydration so a context-sourced
    # override wins but the hardcoded sandbox default still protects us.
    if not CONFIG.mt_demo_user_id:
        CONFIG.mt_demo_user_id = DEFAULT_DEMO_USER_ID

    CONFIG._bootstrapped = True
    return CONFIG
