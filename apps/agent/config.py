"""Typed agent config — Story 1.0.

Two-tier contract (see AC7 in _bmad-output/implementation-artifacts/
1-0-voicerun-cli-polyglot-workspace-scaffold.md):

* Hard-required (raise RuntimeError on import if missing):
    - MT_BASE_URL
    - MT_ACCESS_TOKEN
* Soft (readable, must NOT crash at import; typed Optional[str]):
    - MT_CLIENT_ID
    - MT_CLIENT_SECRET
    - VERIS_AI_API_KEY
* NOT loaded here:
    - VOICERUN_API_KEY       (CI-only; local uses `vr signin` OAuth)
    - NEXT_PUBLIC_DASHBOARD_URL (dashboard-side, see apps/dashboard/lib/config.ts)

Stdlib-only on purpose — requirements.txt stays pinned to `primfunctions`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ENV_FILE = Path(__file__).resolve().parents[2] / ".env.local"


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), value)


_load_env_file(ENV_FILE)


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"Missing required env var: {name} (expected in .env.local)"
        )
    return value


@dataclass(frozen=True)
class AgentConfig:
    mt_base_url: str
    mt_access_token: str
    mt_client_id: str | None
    mt_client_secret: str | None
    veris_ai_api_key: str | None


CONFIG = AgentConfig(
    mt_base_url=_require("MT_BASE_URL"),
    mt_access_token=_require("MT_ACCESS_TOKEN"),
    mt_client_id=os.environ.get("MT_CLIENT_ID"),
    mt_client_secret=os.environ.get("MT_CLIENT_SECRET"),
    veris_ai_api_key=os.environ.get("VERIS_AI_API_KEY"),
)
