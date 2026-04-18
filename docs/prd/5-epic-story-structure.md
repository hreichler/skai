# 5. Epic & Story Structure

The canonical, fully-specified story breakdown (BDD acceptance criteria, source hints, FR coverage map) lives in `docs/epics.md`. This section is the PRD-level summary — keep the two in sync when scope changes.

## Epic 1: Foundational Voice-to-API Loop
Prove end-to-end: scaffold → telephony → handler → logic gate → booking, against the real Mariana Tek sandbox.

- **Story 1.0**: VoiceRun CLI install, polyglot monorepo scaffold (`apps/agent` + `apps/dashboard`), typed `.env.local` config module, `DebugEvent` contract documented in `docs/schema.md`.
- **Story 1.1**: Telephony attachment — real callable phone number routed to the agent handler via the `vr` CLI.
- **Story 1.2**: `async def handler(event, context)` loop — SoHo front-desk greeting, intent capture, session state on `Context`, `TimeoutEvent` handling, <1.5s round-trip latency (NFR1).
- **Story 1.3**: Logic gate — `check_payment_options` tool (FR2) precedes `list_class_sessions` tool (FR1); narrate 2–3 options with implicit confirmation; emit `booking_progress` DebugEvent.
- **Story 1.4**: Happy-path transaction — `create_reservation` tool (FR3) with hardcoded Bearer token (NFR2); verbal confirmation; full lifecycle <60s.

## Epic 2: The "Buddy" Concierge (The Wow Factor)
Sponsor-award differentiators: spot reasoning, guest logic, live-thinking dashboard, judge-configurable schedule, Veris reliability.

- **Story 2.1**: Spot-specific reasoning — map "Bike 5" utterances to `spot_id`; graceful "Spot Unavailable" recovery with neighbor suggestion (NFR3, FR3).
- **Story 2.2**: Buddy booking — `reserved_for_guest` attribute (FR4); two paired reservations in one voice flow; rollback on partial failure.
- **Story 2.3**: Live Thinking dashboard — Next.js/Vercel subscriber to `DebugEvent` stream; renders working memory, STT/TTS, JSON API payloads, and per-turn latency (FR5, NFR1 visible).
- **Story 2.4**: Mid-stream tool call emission — yield `tool_call_start` the instant the LLM decides to call a tool, `tool_call_complete` when it returns. The "money shot" for agentic-reasoning visibility.
- **Story 2.5**: Schedule injection hook — dashboard form writes custom `SCHEDULE_CONFIG` into agent context for scenario stress-testing during the demo.
- **Story 2.6**: Veris AI reliability harness — adversarial-persona simulations across Epic 1 happy path and Epic 2 flows; produces pass/fail report for the "Enterprise Reliability" claim (Goal #3).

## Story Sequencing Notes
- Epic 1 is strictly sequential: 1.0 → 1.1 → 1.2 → 1.3 → 1.4.
- Epic 2 stories 2.3, 2.4, 2.5, 2.6 can be parallelized across two developers once Story 1.0's `DebugEvent` schema is documented. 2.1 and 2.2 depend on Epic 1 being complete.
- A Pre-Demo Readiness Checklist (not a story — a mandatory pass ~30 min before the 3-minute demo) lives in `docs/epics.md#Pre-Demo-Readiness-Checklist`.
