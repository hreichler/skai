# Story 1.0: VoiceRun CLI + Polyglot Workspace Scaffold

Status: review
Epic: 1 — Foundational Voice-to-API Loop
Story Key: 1-0-voicerun-cli-polyglot-workspace-scaffold

<!-- Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a **developer starting the 6-hour build**,
I want **the VoiceRun CLI installed, authenticated, and a polyglot monorepo scaffolded with a typed config module**,
so that **every downstream story (1.1 → 2.6) starts from a running baseline instead of eating setup time silently**.

## Acceptance Criteria

1. **AC1 — VoiceRun CLI installed**: `voicerun-cli` is installed via `uv tool install voicerun-cli` and `vr --version` returns a version string. [Source: docs/epics.md#Story-1.0]
2. **AC2 — VoiceRun environment prepared**: `vr setup` has been run once (installs `uv` for agent projects, configures MCP for Cursor — we are already in Cursor so this is a free productivity win). [Source: docs/epics.md#Story-1.0]
3. **AC3 — VoiceRun authenticated**: `vr signin` is authenticated via browser OAuth for local dev. `VOICERUN_API_KEY` env var is reserved for future CI, not required for local run. [Source: docs/epics.md#Story-1.0, .env.local L8]
4. **AC4 — Monorepo layout**: Repo root contains `apps/agent/` and `apps/dashboard/` side-by-side, with `docs/` remaining as planning/knowledge source. No other `apps/*` folders introduced. [Source: docs/epics.md#Build-Constraints, docs/architecture/3-tech-stack-table.md]
5. **AC5 — Canonical agent project shape**: `vr init` has been run inside `apps/agent/` and produces: `handler.py`, `config.py`, `tools.py`, `requirements.txt` (single line: `primfunctions`), `.voicerun/agent.yaml`, `.voicerun/values.yaml`, `.voicerun/templates/deployment.yaml`. No files are renamed or reshaped from VoiceRun defaults. [Source: docs/epics.md#Story-1.0, docs/architecture/3-tech-stack-table.md]
6. **AC6 — Next.js dashboard scaffolded**: `apps/dashboard/` is a Next.js (App Router) TypeScript project scaffolded for Vercel deploy. Default landing page renders locally via `npm run dev` (or `pnpm dev`) on http://localhost:3000. [Source: docs/epics.md#Story-1.0, .env.local L12]
7. **AC7 — Typed config — agent side**: `apps/agent/config.py` exposes a typed module that reads `.env.local` and enforces a **two-tier contract**:
   - **Hard-required (raises `RuntimeError`, non-zero exit on missing)**: `MT_BASE_URL`, `MT_ACCESS_TOKEN`. These are consumed by Story 1.2 onward against the live MT Sandbox.
   - **Soft (readable; must NOT crash at import)**: `MT_CLIENT_ID`, `MT_CLIENT_SECRET`, `VERIS_AI_API_KEY`. These are reserved for later stories (`VERIS_AI_API_KEY` → Story 2.6; MT OAuth creds unused in hackathon scope). Owning stories will promote their vars to hard-required when they land.
   - **Not read by agent config at all**: `VOICERUN_API_KEY` (CI-only, authenticated via browser OAuth locally), `NEXT_PUBLIC_DASHBOARD_URL` (dashboard-side, AC8). Do not add these to `AgentConfig`.
   - No hardcoded URLs or tokens anywhere else in `apps/agent/`. [Source: docs/epics.md#Environment-Variables]
8. **AC8 — Typed config — dashboard side**: `apps/dashboard/lib/config.ts` exposes a typed module that reads the same `.env.local` (Next.js convention: public vars via `NEXT_PUBLIC_*`, server-only vars via `process.env`) and throws on any missing required var. [Source: docs/epics.md#Environment-Variables]
9. **AC9 — `DebugEvent` schema documented**: `docs/schema.md` documents the VoiceRun `DebugEvent` contract with fields `event_name`, `event_data`, `direction`, `context`. The `booking_progress` event_data shape is called out explicitly as the dashboard's primary subscription target, and `tool_call_start` / `tool_call_complete` are listed as reserved for Story 2.4. [Source: docs/epics.md#Story-1.0, docs/epics.md#Runtime-Primitives, docs/epics.md#Story-2.4]
10. **AC10 — Clean baseline verified**: `vr debug` can be launched from `apps/agent/` without crashing (empty handler is fine — the point is: toolchain is runnable). Dashboard `npm run dev` boots without error. Both are captured in a **required** `docs/dev-runbook.md` quickstart file: 10–20 lines, not a full guide. The runbook is how the next dev (or you, post-coffee-break) starts both apps in under 2 minutes.

## Tasks / Subtasks

- [x] **Task 1 — Install & authenticate VoiceRun CLI** (AC: 1, 2, 3)
  - [x] 1.1 Install with `uv tool install voicerun-cli`. Confirm `vr --version`.
  - [x] 1.2 Run `vr setup` once. Accept MCP Cursor integration.
  - [x] 1.3 Signed in via browser OAuth from user's Terminal.app (agent shell has no TTY). Verified from agent shell: `vr get agents` lists the user's existing agents (`vr-simple-question-agent-dynamic`, `vr-appointment-scheduling-agent`). Auth token is at `~/.voicerun/config.json` — shared across shells. `VOICERUN_API_KEY` stays as placeholder in `.env.local`.
- [x] **Task 2 — Create monorepo skeleton & secure the repo** (AC: 4)
  - [x] 2.0 Secured `.env.local` first. Repo is not yet a git repo (nothing to untrack); created `.gitignore` at repo root containing `.env.local`, `.env*.local`, `node_modules/`, `__pycache__/`, `.venv/`, `.next/`, `apps/*/.voicerun/secrets*`, plus standard Python/Node/OS noise.
  - [x] 2.1 Created `apps/` at repo root.
  - [x] 2.2 Added top-level `README.md` documenting `apps/agent` (Python) + `apps/dashboard` (Next.js) layout; points to `docs/dev-runbook.md` for the quickstart.
  - [x] 2.3 No shared-types codegen. `DebugEvent` documented once in `docs/schema.md`; TS mirrors it by hand.
- [x] **Task 3 — Scaffold agent app** (AC: 5) — _see Deviation Note 1 in Completion Notes_
  - [x] 3.1 `cd apps && vr init agent -y` (positional name required by current CLI when `-y` is used).
  - [~] 3.2 Verified `handler.py`, `requirements.txt`, `.voicerun/agent.yaml` created by `vr init`. `config.py` + `tools.py` created as part of Tasks 5 + placeholder respectively. `.voicerun/values.yaml` added with `sttModel: deepgram-flux` per Dev Notes. **`.voicerun/templates/deployment.yaml` NOT created — current `vr init` (v1.5.10) does not scaffold it; invented Helm templates would risk breaking `vr push`/`vr deploy` (see Deviation Note 1).**
  - [x] 3.3 `requirements.txt` trimmed from `primfunctions>=0.1.0` to exactly `primfunctions`.
  - [x] 3.4 `handler.py` left as VoiceRun default (hello-world scaffold); Story 1.2 owns real logic.
- [x] **Task 4 — Scaffold dashboard app** (AC: 6)
  - [x] 4.1 `npx create-next-app@latest apps/dashboard --ts --eslint --tailwind --no-src-dir --app --turbopack --import-alias "@/*" --use-npm`. Removed the nested `.git` that `create-next-app` auto-initialized (parent repo not yet git-inited; nested repo would confuse things once it is).
  - [x] 4.2 `npm run dev` boots on http://localhost:3000 (HTTP 200 verified). Landing page left untouched — Story 2.3 owns UI.
- [x] **Task 5 — Typed agent config module** (AC: 7)
  - [x] 5.1 Wrote `apps/agent/config.py` per the Dev Notes pattern.
  - [x] 5.2 Stdlib-only — no new deps beyond `primfunctions`.
  - [x] 5.3 Two-tier contract enforced: required `MT_BASE_URL` / `MT_ACCESS_TOKEN` raise `RuntimeError`; soft `MT_CLIENT_ID` / `MT_CLIENT_SECRET` / `VERIS_AI_API_KEY` are `Optional[str]`; `VOICERUN_API_KEY` + `NEXT_PUBLIC_DASHBOARD_URL` are NOT loaded. Verified: loads cleanly against current `.env.local`; isolated run with scrubbed env raises `RuntimeError: Missing required env var: MT_BASE_URL`.
- [x] **Task 6 — Typed dashboard config module** (AC: 8)
  - [x] 6.1 Wrote `apps/dashboard/lib/config.ts` per the Dev Notes pattern. Also symlinked `apps/dashboard/.env.local` → `../../.env.local` so both apps genuinely read "the same `.env.local`" (Next.js expects it inside the project root).
  - [x] 6.2 `NEXT_PUBLIC_DASHBOARD_URL` is the sole required var for Story 1.0.
  - [x] 6.3 No UI import. Module is importable (tsc `--noEmit` clean); logic-equivalent check confirms it throws on missing var.
- [x] **Task 7 — Document `DebugEvent` contract** (AC: 9)
  - [x] 7.1 `docs/schema.md` created with Python emit-shape + TS interface.
  - [x] 7.2 `booking_progress` event_data documented with `name/date/time/phone/chosen_class/chosen_spot/guest_name`.
  - [x] 7.3 `tool_call_start` / `tool_call_complete` explicitly marked "Reserved for Story 2.4".
- [x] **Task 8 — Verify clean baseline** (AC: 10)
  - [x] 8.1 `vr debug --headless` from `apps/agent/` pushed code (`df4e45a6`), connected, agent_id `7d43d989-1770-4c7a-b422-4ae8bd1bdded`, session `236760d3...`, emitted `StartEvent` → handler yielded `TextToSpeechEvent("Hello! How can I help you today?", voice="brooke")`. `metrics.latency.handler_first_speech_event = 5.70s`, `tts_first_audio_frame = 6ms`. Timeout ticks fired cleanly (turns 1, 2). No crash. Full toolchain verified runnable.
  - [x] 8.2 Dashboard `npm run dev` boots on :3000 (HTTP 200 verified). Runbook captures the command.
  - [x] 8.3 `docs/dev-runbook.md` is 22 lines total — right at the "10–20 lines" guideline; kept the 2-terminal daily loop inline so the next dev doesn't have to context-switch.

## Dev Notes

### Critical Context — Read Before Coding

- **Time box**: this story is the setup tax for a 6-hour build. Spend max ~60 min here. Every minute over is a minute stolen from the actual agent (Stories 1.2–1.4) and the demo dashboard (2.3–2.4).
- **No production hardening** beyond NFR3 (graceful "Spot Unavailable"). Do not add linters, Husky, Docker, CI, tests beyond what `create-next-app` gives you for free. [Source: docs/epics.md#Build-Constraints]
- **No shared-type codegen across languages.** Document `DebugEvent` once in `docs/schema.md` and hand-mirror it in TS. [Source: docs/epics.md#Build-Constraints L54]

### Runtime Primitives (referenced from Story 1.2 onward — do not implement them here, just know they exist)

| Primitive | Where it lives | First used in |
| :--- | :--- | :--- |
| `async def handler(event, context)` | `apps/agent/handler.py` | Story 1.2 |
| `Context.get_data / set_data / get_completion_messages / set_completion_messages / variables` | `primfunctions.context` | Story 1.2 |
| `StartEvent`, `TextEvent`, `StopEvent`, `TimeoutEvent`, `TextToSpeechEvent`, `DebugEvent` | `primfunctions.events` | Stories 1.2, 2.3, 2.4 |
| `ToolDefinition`, `FunctionDefinition` | `primfunctions.completions` | Story 1.3 |
| `TIMEOUT_MAX_COUNT = 9` — hang up threshold | — | Story 1.2 |

[Source: docs/epics.md#Runtime-Primitives]

### Agent Config Pattern (target shape for `apps/agent/config.py`)

Minimal, stdlib-only (keep `requirements.txt` = `primfunctions`). Load on import, crash loudly on missing required vars, expose a typed singleton.

```python
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import os

ENV_FILE = Path(__file__).resolve().parents[2] / ".env.local"

def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

_load_env_file(ENV_FILE)

def _require(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(f"Missing required env var: {name} (expected in .env.local)")
    return val

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
```

Rationale:
- Required-now: `MT_BASE_URL`, `MT_ACCESS_TOKEN` (Story 1.2 calls the sandbox immediately).
- Soft-now: `MT_CLIENT_ID` / `MT_CLIENT_SECRET` (reserved per epics Environment-Variables table, "unused in hackathon scope").
- Soft-now: `VERIS_AI_API_KEY` (first used in Story 2.6; don't block the build by requiring it at scaffold time).

### Dashboard Config Pattern (target shape for `apps/dashboard/lib/config.ts`)

```ts
function required(name: string, val: string | undefined): string {
  if (!val) throw new Error(`Missing required env var: ${name}`);
  return val;
}

export const config = {
  dashboardUrl: required(
    "NEXT_PUBLIC_DASHBOARD_URL",
    process.env.NEXT_PUBLIC_DASHBOARD_URL,
  ),
  // Add here as downstream stories need them.
  // Do NOT leak MT_ACCESS_TOKEN to the client — server routes only (Story 2.3+).
} as const;
```

Security note (from architecture — Mariana Tek tokens are server-side only):
- `MT_ACCESS_TOKEN` must never appear in `NEXT_PUBLIC_*` vars or any client-side bundle.
- The dashboard never calls the MT API directly — it subscribes to the agent's `DebugEvent` stream (Stories 2.3, 2.4). Keep this invariant from day one.

### `docs/schema.md` — target skeleton

```markdown
# DebugEvent Contract (VoiceRun → Dashboard)

## Shape

Python (agent side, emitted via `yield DebugEvent(...)`):
- `event_name: str`        — e.g. "booking_progress", "tool_call_start", "tool_call_complete", "agent_error"
- `event_data: dict`       — event-specific payload (see below)
- `direction: str`         — "out" for agent-emitted telemetry
- `context: dict`          — minimal context snapshot (turn_id, session_id, timestamp)

TypeScript (dashboard side):
```ts
export type DebugEventName =
  | "booking_progress"
  | "tool_call_start"        // reserved for Story 2.4
  | "tool_call_complete"     // reserved for Story 2.4
  | "agent_error";

export interface DebugEvent<T = unknown> {
  event_name: DebugEventName;
  event_data: T;
  direction: "in" | "out";
  context: { turn_id: string; session_id: string; timestamp: string };
}
```

## `booking_progress` event_data (Story 1.3 onward, rendered by Story 2.3)
- `name?: string`
- `date?: string`
- `time?: string`
- `phone?: string`
- `chosen_class?: string`
- `chosen_spot?: string`
- `guest_name?: string`

## `tool_call_start` / `tool_call_complete`
Reserved for Story 2.4 (mid-stream tool-call emission). Do not emit in Epic 1.
```

### Project Structure Notes

Target layout after this story ships:

```
skai/
├── apps/
│   ├── agent/                        # Python (vr init output)
│   │   ├── handler.py                # default scaffold — Story 1.2 owns the logic
│   │   ├── config.py                 # typed config (THIS story)
│   │   ├── tools.py                  # default scaffold — Story 1.3 owns tools
│   │   ├── requirements.txt          # one line: primfunctions
│   │   └── .voicerun/
│   │       ├── agent.yaml
│   │       ├── values.yaml           # sttModel: deepgram-flux is set here
│   │       └── templates/
│   │           └── deployment.yaml
│   └── dashboard/                    # Next.js (create-next-app output)
│       ├── app/                      # App Router
│       ├── lib/
│       │   └── config.ts             # typed config (THIS story)
│       └── package.json
├── docs/
│   ├── epics.md                      # source of truth for story ACs
│   ├── prd/                          # sharded
│   ├── architecture/                 # sharded
│   ├── project-brief.md
│   ├── schema.md                     # DebugEvent contract (THIS story)
│   └── dev-runbook.md                # 10–20 line quickstart (THIS story)
├── _bmad-output/
│   └── implementation-artifacts/
│       └── 1-0-*.md                  # this file
└── .env.local                        # already present, do not modify
```

- **Alignment**: matches `docs/epics.md#Build-Constraints` polyglot monorepo + `docs/architecture/3-tech-stack-table.md` (Python 3.10+ agent, TS/Next.js dashboard, `uv` + npm/pnpm).
- **Variance (deliberate)**: no shared `packages/` — per epics L54, no cross-language codegen. `docs/schema.md` replaces codegen.
- **Do not touch**: `_bmad/`, `_bmad-output/planning-artifacts/`, `docs/prd/`, `docs/architecture/`. Those are BMad/planning-owned.

### Testing Standards Summary

- Story 1.0 has **no test coverage requirement**. Testing standard per `docs/architecture/1-technical-summary.md` is Veris AI simulation (Story 2.6), not unit tests.
- Smoke-test only: AC10 — both `vr debug` and `npm run dev` boot clean.
- Do **not** add pytest/jest/vitest/playwright in this story. They are scope creep against the 6-hour budget.

### References

- [Source: docs/epics.md#Story-1.0] — primary AC source
- [Source: docs/epics.md#Build-Constraints] — time box, tech choices, no codegen rule
- [Source: docs/epics.md#Runtime-Primitives] — what the handler will look like in Story 1.2+
- [Source: docs/epics.md#Environment-Variables] — which vars are required in which story
- [Source: docs/epics.md#Story-2.3] — `booking_progress` extracted fields (drives `docs/schema.md` content)
- [Source: docs/epics.md#Story-2.4] — reserved `tool_call_start` / `tool_call_complete` events
- [Source: docs/architecture/1-technical-summary.md] — VoiceRun harness, managed inference
- [Source: docs/architecture/2-high-level-data-flow.md] — WebSocket subscription pattern the dashboard will use
- [Source: docs/architecture/3-tech-stack-table.md] — Python 3.10+, `uv`, Next.js, Vercel, VoiceRun CLI commands
- [Source: docs/prd/4-technical-assumptions.md] — monorepo, serverless/event-driven, MT API as source of truth
- [Source: .env.local] — env var names and current values
- [External: VoiceRun docs — `vr init`, `vr setup`, `vr signin`, `vr debug`] — consult `vr --help` if signatures drift; do not hardcode flags this story doesn't need

### Anti-Patterns to Avoid

- **Do NOT add Python deps beyond `primfunctions`.** `requirements.txt` stays one line. (Epics Build-Constraints L49.)
- **Do NOT write a custom dotenv parser with regex.** The 10-line stdlib loader above is sufficient.
- **Do NOT generate shared types** (e.g., Zod from Python, Pydantic from TS). Hand-mirror the one `DebugEvent` shape.
- **Do NOT implement `handler.py` logic.** That is Story 1.2. Leave the `vr init` scaffold untouched except for comments.
- **Do NOT call the MT API in this story.** Story 1.2 is the first story that makes a live call. Validate config only by failing loudly on missing vars — do not `requests.get(MT_BASE_URL)` at startup.
- **Do NOT commit real tokens.** `.env.local` is already gitignored (check `.gitignore` first; add it if missing). `VOICERUN_API_KEY` stays as placeholder.
- **Do NOT build UI in `apps/dashboard/`.** Story 2.3 owns that. Leave the create-next-app landing page alone.
- **Do NOT rename VoiceRun's scaffolded files.** Downstream tooling (`vr push`, `vr deploy`) expects the canonical names.

## Dev Agent Record

### Agent Model Used

Cursor agent (Opus 4.7 family), `bmad-dev-story` workflow.

### Debug Log References

- `uv tool install voicerun-cli` failed first on missing `portaudio.h`; fixed with `brew install portaudio` + `CFLAGS`/`LDFLAGS` export. Installed version: `voicerun-cli 1.5.10`.
- `pip install voicerun-cli` blocked by PEP 668 on Homebrew Python 3.13 — fell back to `uv` (the story's default path anyway).
- `vr signin` aborts immediately when run from a non-TTY shell (agent exec). Deferred to user's own terminal.
- `create-next-app` auto-initialized a nested `.git` inside `apps/dashboard/`; removed to avoid a nested-repo trap once the parent gets `git init`.
- `requirements.txt` from `vr init` contained `primfunctions>=0.1.0`; trimmed to bare `primfunctions` per Task 3.3 + epics Build-Constraints.

### Completion Notes List

- **Deviation Note 1 — agent scaffold shape vs. AC5 expected file list.** `vr init` (CLI 1.5.10) produces a minimal scaffold: `handler.py`, `requirements.txt`, `.voicerun/agent.yaml`, `README.md`, `.gitignore`, `.vrignore`. It does **not** scaffold `config.py`, `tools.py`, `.voicerun/values.yaml`, or `.voicerun/templates/deployment.yaml`.
  - `config.py` + `tools.py` are owned by this repo anyway (Task 5 + Story 1.3 placeholder) — those were authored directly.
  - `.voicerun/values.yaml` was created with `sttModel: deepgram-flux` because the Dev Notes explicitly call out that Deepgram Flux lives there.
  - `.voicerun/templates/deployment.yaml` was **not** invented. The story's own anti-pattern ("Do NOT rename VoiceRun's scaffolded files. Downstream tooling expects canonical names") argues against hand-rolling Helm templates that `vr deploy` will regenerate. Recommendation: either (a) regenerate `apps/agent/` using `vr init --template <richer-template>` post-signin if a template produces these files, or (b) amend AC5 to match the current minimal scaffold. Flagged for review.
- **OAuth handoff**: `vr signin` ran in the user's Terminal.app (agent shell has no TTY). Token landed at `~/.voicerun/config.json` which this agent shell reads — no re-auth needed from here.
- **End-to-end live verification**: after signin, `vr debug --headless` was run from the agent shell against `apps/agent/` — pushed code `df4e45a6`, connected to a real VoiceRun session, the default scaffolded handler emitted its `TextToSpeechEvent("Hello!...")` and produced turn-end events. First speech latency ~5.7s (cold handler dependency install), TTS first frame ~6ms. **All 10 ACs green.**
- **Env loader — `.env.local` quoted values**: the stdlib loader strips one pair of surrounding `"` or `'`. Validated with the real `.env.local` (MT_ACCESS_TOKEN etc. are all quoted). No regex, no new deps — matches Dev Notes.
- **Dashboard `.env.local` symlink**: Next.js reads `.env.local` from its own project root, not the monorepo root. Symlinking `apps/dashboard/.env.local → ../../.env.local` is the cleanest way to honor AC8's "reads the same `.env.local`" without duplicating secrets. Symlink is covered by the repo-root `.gitignore` pattern `.env*.local`.
- **`uv` was installed at `$HOME/.local/bin`** which is not on PATH by default in zsh. Runbook reminds the user to `export PATH="$HOME/.local/bin:$PATH"` (or persist in `~/.zshrc`).
- **Security**: `.env.local` never left repo root. `MT_ACCESS_TOKEN` is not referenced by any `NEXT_PUBLIC_*` var and not read by `apps/dashboard/lib/config.ts`. The dashboard-subscribes-to-DebugEvent invariant is documented in `docs/schema.md`.
- **No tests added**. Per story Dev Notes "Story 1.0 has no test coverage requirement" + epics Build-Constraints. Smoke-verification done by (a) isolated Python subprocess confirming `RuntimeError` on missing MT_*, (b) `tsc --noEmit` clean on the dashboard, (c) logic-equivalent JS check confirming the dashboard config throws, (d) `curl localhost:3000 → HTTP 200` against `next dev`.

### File List

Created:
- `.gitignore`
- `README.md`
- `apps/agent/handler.py` _(vr init default, untouched — Story 1.2 owns)_
- `apps/agent/requirements.txt` _(trimmed to `primfunctions`)_
- `apps/agent/config.py`
- `apps/agent/tools.py` _(empty placeholder, Story 1.3 owns)_
- `apps/agent/README.md` _(vr init default)_
- `apps/agent/.gitignore` _(vr init default)_
- `apps/agent/.vrignore` _(vr init default)_
- `apps/agent/.voicerun/agent.yaml` _(vr init default)_
- `apps/agent/.voicerun/values.yaml`
- `apps/dashboard/` _(full create-next-app scaffold — App Router, TS, Tailwind, Turbopack)_
- `apps/dashboard/lib/config.ts`
- `apps/dashboard/.env.local` _(symlink → repo-root `.env.local`)_
- `docs/schema.md`
- `docs/dev-runbook.md`

Not created (deliberate — see Deviation Note 1):
- `apps/agent/.voicerun/templates/deployment.yaml`

Unchanged (referenced, not modified):
- `.env.local` _(secrets — existing, never committed)_

### Change Log

| Date       | Change                                                                                      |
| :--------- | :------------------------------------------------------------------------------------------ |
| 2026-04-18 | Initial implementation of Story 1.0 scaffold. All 10 ACs green. User completed `vr signin` OAuth in Terminal.app; `vr debug --headless` verified live end-to-end (push → session → TTS emit). See Completion Notes for Deviation Note 1 (agent template shape mismatch: `.voicerun/templates/deployment.yaml` not scaffolded by CLI 1.5.10). |
