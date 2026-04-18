---
stepsCompleted:
  - step-01-validate-prerequisites
  - step-02-design-epics
  - step-03-author-stories
  - step-04-pre-kickoff-hardening
  - step-05-voicerun-grounding
  - step-06-baseten-removal
inputDocuments:
  - docs/prd/index.md
  - docs/prd/1-goals-and-background-context.md
  - docs/prd/2-requirements.md
  - docs/prd/3-user-interface-design-goals.md
  - docs/prd/4-technical-assumptions.md
  - docs/prd/5-epic-story-structure.md
  - docs/architecture/index.md
  - docs/architecture/1-technical-summary.md
  - docs/architecture/2-high-level-data-flow.md
  - docs/architecture/3-tech-stack-table.md
  - docs/project-brief.md
  - .env.local
  - VoiceRun CLI getting-started (external)
  - VoiceRun agent repo reconnaissance (handler.py, tools.py, .voicerun/*.yaml)
---

# Epics: Mariana Tek AI Voice Concierge

## Overview

This document is the hackathon-ready implementation roadmap for the Mariana Tek AI Voice Concierge. It decomposes the PRD and Architecture into two epics sized for a 6-hour build and a 3-minute demo. The Scrum Master (@sm) uses this to guide the Developer (@dev) via `*create epic-1`.

## Runtime Primitives (read this first)

These names appear in every story AC. Grounding them here once avoids hand-waving later.

| Primitive | What it is | Where it lives |
| :--- | :--- | :--- |
| `async def handler(event, context)` | The agent. A generator function. Events in, events out. | `apps/agent/handler.py` |
| `Context` | Per-session state. `context.get_data/set_data` for arbitrary state, `context.get_completion_messages/set_completion_messages` for LLM history, `context.variables` for deployment-injected config (e.g., `SCHEDULE_CONFIG`). | `primfunctions.context` |
| `StartEvent` / `TextEvent` / `StopEvent` / `TimeoutEvent` | Inbound events to the handler. | `primfunctions.events` |
| `TextToSpeechEvent` | Agent speaks: `yield TextToSpeechEvent(text=..., voice="kore")`. | `primfunctions.events` |
| `DebugEvent` | Structured telemetry out of the agent: `{event_name, event_data, direction, context}`. This is the dashboard's event contract — we inherit it, we don't invent `AgentEvent`. | `primfunctions.events` |
| `ToolDefinition` / `FunctionDefinition` | OpenAI-style function calling schema. MT API calls are tools. | `primfunctions.completions` |
| `TimeoutEvent.count` | Per-turn dead-air counter. Prompt at every 3rd count, hang up at `TIMEOUT_MAX_COUNT = 9`. Barge-in / silence handling lives here. | — |

## Build Constraints

- **Time box**: 6-hour build, 3-minute demo — stories stay thin; no production hardening beyond NFR3.
- **Agent language**: Python 3.10+ via the `primfunctions` SDK (bundled with VoiceRun runtime). `requirements.txt` is one line: `primfunctions`.
- **Dashboard language**: TypeScript / Next.js / Vercel.
- **MT API**: Real Mariana Tek sandbox is live with working auth tokens, base URL, and seeded test users. Agent calls the sandbox directly from Story 1.2 onward.
- **LLM / STT / TTS**: VoiceRun-managed (Gemini 3 Flash Preview, Deepgram Flux). No API keys or billing owned by us.
- **Session state**: Held on `Context` per call. No Redis, no external store for MVP.
- **Shared types**: No cross-language codegen. Document the VoiceRun `DebugEvent` shape once in `docs/schema.md`; the dashboard consumes it directly as a typed DTO on the TS side.

## Environment Variables

Defined in `.env.local` (confirmed present). Every story that calls an external service must read these via a typed config module — no hardcoded URLs or keys in story implementations.

| Variable | Purpose | First Used In |
| :--- | :--- | :--- |
| `MT_BASE_URL` | Mariana Tek sandbox base URL | Story 1.2 |
| `MT_ACCESS_TOKEN` | Bearer token for Demo User (NFR2) | Story 1.2 |
| `MT_CLIENT_ID` / `MT_CLIENT_SECRET` | MT OAuth credentials (reserved, unused in hackathon scope) | — |
| `VOICERUN_API_KEY` | `vr signin --api-key` (CI/automation). Interactive browser OAuth for dev. | Story 1.0 |
| `VERIS_AI_API_KEY` | Veris AI simulation harness | Story 2.6 |
| `NEXT_PUBLIC_DASHBOARD_URL` | Dashboard URL for WebSocket handshake / local dev | Story 2.3 |

## Requirements Inventory

### Functional Requirements

- **FR1**: Query `GET /class_sessions/` to narrate available times based on voice.
- **FR2**: Check `GET /payment_options/` before booking to ensure user eligibility.
- **FR3**: Execute `POST /reservations/` with specific `spot` ID relationships.
- **FR4**: Support "Buddy Booking" using the `reserved_for_guest` attribute.
- **FR5**: Provide a visual web dashboard showing live STT/TTS and JSON API logs.

### Non-Functional Requirements

- **NFR1**: Voice response latency < 1.5s via VoiceRun's managed inference pipeline (Gemini 3 Flash Preview + Deepgram Flux).
- **NFR2**: Use hardcoded "Demo User" Bearer Token for immediate sandbox auth.
- **NFR3**: Gracefully handle "Spot Unavailable" errors from Mariana Tek.

### Additional Requirements (Architecture + Reconnaissance)

- Polyglot monorepo: `apps/agent` (Python) + `apps/dashboard` (Next.js), plus root `docs/schema.md` documenting `DebugEvent` contract.
- Serverless / event-driven runtime via VoiceRun (Helm/K8s under the hood, `values.yaml` swap per env).
- Session context preserved across turns within a single call via `Context.get_data/set_data`.
- Veris AI adversarial-persona simulation validates "Enterprise Reliability" goal.
- Mariana Tek Admin API (REST) is the single source of truth.
- Agent emits `DebugEvent(event_name='booking_progress', ...)` on every turn via a cheap zero-temperature extraction pass — dashboard subscribes to this stream.

### UX Design Requirements

No standalone UX Design Specification exists. UX direction is inline in PRD §3:

- High-end SoHo studio front-desk persona — proactive, efficient, never robotic.
- **Implicit confirmation** over interrogation ("Great, I've got you on Bike 12 for 6 PM" — not "Would you like me to book Bike 12?").
- **Voice states**: Greeting (capture intent) → Availability (narrate 2–3 options) → Execution (final handshake / confirmation).

### FR Coverage Map

| Requirement | Covered By |
| :--- | :--- |
| FR1 — Narrate class availability | Story 1.3 |
| FR2 — Payment eligibility gate | Story 1.3 |
| FR3 — Reservation with spot ID | Story 1.4, Story 2.1 |
| FR4 — Buddy booking (`reserved_for_guest`) | Story 2.2 |
| FR5 — Live dashboard | Story 2.3, Story 2.4 (mid-stream tool calls) |
| NFR1 — <1.5s latency | Story 1.2 (baseline), Story 2.3 (instrumented & displayed) |
| NFR2 — Hardcoded Bearer token | Story 1.0 (typed config), Story 1.4 (usage) |
| NFR3 — Graceful "Spot Unavailable" handling | Story 2.1 |
| Arch — Polyglot monorepo | Story 1.0 |
| Arch — Telephony attachment | Story 1.1 |
| Arch — Session context via `Context` | Story 1.2 |
| Arch — `DebugEvent` contract | Story 2.3 |
| Arch — Veris AI reliability | Story 2.6 |
| Agentic Reasoning (Goal #2) | Story 1.3, Story 1.4, Story 2.4 |

## Epic List

1. **Epic 1 — Foundational Voice-to-API Loop**: Scaffold the polyglot monorepo, attach telephony, and prove the end-to-end voice-to-booking lifecycle against the real MT sandbox using VoiceRun's `primfunctions` primitives.
2. **Epic 2 — The "Wow" Factor (Buddy Concierge)**: Advanced spot reasoning, buddy booking, live-thinking visualizer with mid-stream tool visibility, judge-configurable schedule injection, and Veris reliability validation.

---

## Epic 1: Foundational Voice-to-API Loop

**Goal**: Scaffold the polyglot monorepo, attach a real phone number, and establish a functional conversation where the agent identifies a user and books a single class session against the real MT Sandbox — end-to-end, within <1.5s voice latency per turn.

### Story 1.0: VoiceRun CLI + Polyglot Workspace Scaffold

As a **developer starting the 6-hour build**,
I want **the VoiceRun CLI installed, authenticated, and a polyglot monorepo scaffolded with a typed config module**,
So that **every downstream story starts from a running baseline instead of eating setup time silently**.

**Acceptance Criteria:**

**Given** a fresh repo at the project root and Python 3.10+ available
**When** scaffold is complete
**Then** `voicerun-cli` is installed via `uv tool install voicerun-cli` and `vr --version` returns a version
**And** `vr setup` has run (installs uv for agent projects, configures MCP for Cursor — we're already in Cursor so this is a free productivity win)
**And** `vr signin` is authenticated (browser OAuth for dev; API key reserved for CI)
**And** the repo contains `apps/agent/` and `apps/dashboard/` side-by-side
**And** `vr init` has been run inside `apps/agent/` and produces the canonical VoiceRun project shape: `handler.py`, `config.py`, `tools.py`, `requirements.txt` (single line: `primfunctions`), and `.voicerun/agent.yaml`, `.voicerun/values.yaml`, `.voicerun/templates/deployment.yaml`
**And** `apps/dashboard/` is a Next.js project scaffolded for Vercel deploy
**And** a typed config module (`apps/agent/config.py` + `apps/dashboard/lib/config.ts`) reads `.env.local` variables listed in the Environment Variables table and fails loudly on any missing required var
**And** `docs/schema.md` documents the VoiceRun `DebugEvent` contract (`event_name`, `event_data`, `direction`, `context`) with the `booking_progress` event_data shape called out explicitly as the dashboard's primary subscription target.

### Story 1.1: Telephony Attachment

As a **presenter who needs a callable phone number for the demo**,
I want **a real phone number attached to the agent through the `vr` CLI with a telephony provider configured**,
So that **judges can dial in and hear the agent live — no emulator, no simulation**.

**Acceptance Criteria:**

**Given** Story 1.0 is complete and `vr signin` is authenticated
**When** a phone number is attached to the agent via the VoiceRun CLI phone-number management commands
**Then** a callable phone number is provisioned and associated with the agent
**And** a telephony provider is configured such that inbound calls to that number route to the agent's handler
**And** a manual dial test from a real phone triggers the handler and produces a greeting (even if the rest of the flow is a stub at this point).

### Story 1.2: Handler Loop + Session Context + Intent Discovery

As a **studio member calling the concierge line**,
I want **the agent to answer in the SoHo front-desk persona, understand I want to book a class, and remember what I say across turns**,
So that **I don't hit IVR friction and I don't have to repeat myself mid-conversation**.

**Acceptance Criteria:**

**Given** `apps/agent/handler.py` defines `async def handler(event, context)` using `primfunctions`
**When** a caller dials in (Story 1.1) and says "I want to book a class"
**Then** the handler processes `StartEvent` and greets the caller in the SoHo front-desk persona via `yield TextToSpeechEvent(text=..., voice="kore")`
**And** the "Book class" intent is extracted and persisted via `context.set_data("intent", "book_class")`
**And** the handler maintains session state on `Context` (current intent, captured slots, chosen class, chosen spot, guest name) accessible to every tool invocation and every subsequent turn
**And** `TimeoutEvent` is handled: the handler prompts the caller at every 3rd count and hangs up cleanly at `TIMEOUT_MAX_COUNT = 9`
**And** round-trip voice latency (user utterance → agent first spoken token) measures under 1.5s (NFR1) during `vr debug` testing.

### Story 1.3: Logic Gate — Payment & Discovery

As a **studio member with an active credit pack**,
I want **the agent to narrate available class times only when I'm eligible to book**,
So that **I don't get offered options I can't purchase**.

**Acceptance Criteria:**

**Given** the agent has identified the "Book class" intent (Story 1.2)
**When** the agent's reasoning step needs class options
**Then** the agent invokes a `check_payment_options` tool (OpenAI-style `ToolDefinition` wrapping `GET /payment_options/`) as a logic gate (FR2)
**And** if the caller has active credits, the agent invokes a `list_class_sessions` tool (wrapping `GET /class_sessions/`) and narrates 2–3 options using implicit confirmation phrasing (FR1, PRD §3)
**And** if the caller has no active credits, the agent gracefully explains and does not proceed to class narration
**And** the selected class is persisted via `context.set_data("selected_class", ...)` for Story 1.4 to consume
**And** a `DebugEvent(event_name='booking_progress', ...)` is emitted after this turn reflecting updated working memory.

### Story 1.4: Happy Path Transaction

As a **studio member who has chosen a class**,
I want **the agent to book it and verbally confirm**,
So that **I'm done in under 60 seconds without touching an app**.

**Acceptance Criteria:**

**Given** the caller has selected a class (Story 1.3) and the selection is on `context`
**When** the agent confirms the final intent
**Then** the agent invokes a `create_reservation` tool (wrapping `POST /reservations/`) with `MT_ACCESS_TOKEN` (NFR2) and the selected class relationship (FR3)
**And** on `201 Created` the agent verbally confirms ("Great, you're booked for the 6 PM Ride")
**And** the full lifecycle (intent → payment gate → narrate → book → confirm) completes in <60s end-to-end during a `vr debug` run.

---

## Epic 2: The "Wow" Factor (Buddy Concierge)

**Goal**: Deliver advanced spot-level reasoning, guest coordination, a live-thinking visual dashboard with mid-stream tool visibility, judge-configurable schedule injection, and Veris AI reliability validation — the differentiators that win sponsor awards in a 3-minute demo.

### Story 2.1: Spot-Specific Reasoning

As a **spin-class regular with a favorite bike**,
I want **to request "Bike 5" by voice and have the agent handle it gracefully if taken**,
So that **I get my preferred spot or a sensible alternative without re-prompting**.

**Acceptance Criteria:**

**Given** a caller says "Book me on Bike 5 for the 6 PM class"
**When** the agent maps the utterance to a specific `spot_id` from the classroom layout
**Then** the agent invokes `create_reservation` with that `spot` relationship (FR3)
**And** if MT returns a "Spot Unavailable" error, the agent suggests a neighbor spot (e.g., "Bike 5 is taken — Bike 4 is open, want that?") without hanging up (NFR3)
**And** the fallback spot is selected from the same classroom layout (not guessed)
**And** spot state transitions are reflected in the `booking_progress` DebugEvent for dashboard visibility.

### Story 2.2: Buddy Booking Logic

As a **member who rides with a friend**,
I want **to book myself and my guest in one voice flow**,
So that **we get paired spots without me navigating an app**.

**Acceptance Criteria:**

**Given** a caller says something like "Book me and my friend Sarah for the 6 PM"
**When** the agent recognizes the buddy-booking intent
**Then** the guest name is captured into `context.set_data("guest_name", "Sarah")` and the agent creates two reservations in a single flow — one for the primary user, one using the `reserved_for_guest` attribute (FR4)
**And** the agent verbally confirms both bookings ("You and Sarah are on Bikes 11 and 12 at 6")
**And** if either reservation fails, the agent rolls back or cleanly announces the partial state rather than leaving one orphaned booking silently.

### Story 2.3: Live Thinking Dashboard (DebugEvent Subscriber)

As a **hackathon judge watching the demo**,
I want **a live dashboard that renders the agent's working memory, API calls, and per-turn latency in real time**,
So that **I can see the agentic reasoning as it happens and verify the <1.5s claim**.

**Acceptance Criteria:**

**Given** a Next.js app is deployed on Vercel (or tunneled via ngrok for local demo) and subscribed to the agent's `DebugEvent` stream
**When** the voice agent processes a call
**Then** the dashboard renders every `DebugEvent(event_name='booking_progress', ...)` payload as the agent's live working memory (extracted fields: name, date, time, phone, chosen class, chosen spot, guest name)
**And** the dashboard renders live STT transcripts (caller speech in) and TTS outputs (agent speech out) alongside the working memory panel
**And** each MT API call's JSON request/response is rendered (FR5) correlated to the turn that triggered it
**And** each turn is timestamped and the dashboard displays measured round-trip voice latency per turn, visibly validating NFR1 (<1.5s)
**And** payloads appear within 500ms of the underlying event so the visual stays in sync with audio during the 3-minute demo.

### Story 2.4: Mid-Stream Tool Call Emission (The Money Shot)

As a **hackathon judge watching the agent reason**,
I want **to see each tool invocation light up on the dashboard the instant the agent decides to call it, not after it completes**,
So that **I witness "the AI is calling `check_availability` right now" — the visual that makes agentic reasoning feel real**.

**Acceptance Criteria:**

**Given** the handler's tool loop (`_run_completion`) currently collects `tool_call` stream chunks internally and emits `booking_progress` only after tools finish
**When** a tool-call chunk is detected in the LLM stream
**Then** the handler immediately yields a `DebugEvent(event_name='tool_call_start', event_data={tool_name, arguments_preview, turn_id, timestamp})` before executing the tool
**And** after the tool returns, the handler yields `DebugEvent(event_name='tool_call_complete', event_data={tool_name, result_summary, duration_ms, turn_id})`
**And** the dashboard renders both events distinctly — `tool_call_start` as a "thinking" indicator, `tool_call_complete` as a resolved result
**And** the change is confined to the `_run_completion` tool-loop path (estimated ≤10 lines of agent code).

### Story 2.5: Schedule Injection Hook

As a **judge or sponsor wanting to stress-test the agent with a specific scenario**,
I want **a dashboard-side form that injects a custom schedule into the agent's context before a call**,
So that **I can paste in "a full class at 6 PM with one open spot on Bike 7" and watch the agent navigate it live**.

**Acceptance Criteria:**

**Given** the agent already reads `SCHEDULE_CONFIG` from `context.variables` (deployment-injected) or `context.get_data` (runtime-injected) per the reconnaissance
**When** a user pastes a schedule JSON into a dashboard form and submits it
**Then** the schedule is written into the agent's context via the runtime-injection path (`context.set_data('SCHEDULE_CONFIG', ...)` or equivalent documented mechanism) before the next call
**And** the next inbound call honors the injected schedule instead of (or alongside) real MT data
**And** the dashboard clearly labels when the agent is running on an injected schedule vs. the real sandbox.

### Story 2.6: Veris AI Reliability Harness

As a **team pitching "Enterprise Reliability" as Goal #3**,
I want **Veris AI running adversarial-persona simulations against the agent**,
So that **I can claim validated robustness on stage with a concrete pass number instead of hand-waving it**.

**Acceptance Criteria:**

**Given** `VERIS_AI_API_KEY` is configured (Story 1.0) and the deployed agent endpoint is reachable
**When** the Veris harness is invoked with at least three adversarial personas (e.g., impatient interrupter, mumbler, mid-call-mind-changer)
**Then** each persona runs the full Epic 1 happy path (book a class) and at least one Epic 2 flow (spot-specific or buddy)
**And** a pass/fail report is produced showing which personas completed the booking lifecycle end-to-end
**And** the "Happy Path" persona passes at 100% (PRD Goal #3), and any failures are captured with root-cause pointers usable in the demo narrative.

---

## Pre-Demo Readiness Checklist

Not a story — a mandatory pre-demo pass. Run this with @sm ~30 min before the 3-minute demo. If any item is red, escalate or cut it from the demo narrative.

- [ ] Epic 1 happy path (call → book → confirm) runs end-to-end against the real sandbox, three times in a row, no retries.
- [ ] Epic 2 Story 2.1 (spot-taken recovery) can be reliably triggered — pre-seed or identify a spot that's known-taken in the sandbox.
- [ ] Epic 2 Story 2.2 (buddy booking) produces two visible reservations in MT Admin.
- [ ] Dashboard (Stories 2.3 + 2.4) is deployed to Vercel (not localhost) and shows `booking_progress` + `tool_call_start`/`tool_call_complete` events in real time with latency < 1.5s displayed per turn.
- [ ] Schedule injection (Story 2.5) pre-configured with a "stressful" demo scenario for the second pitch minute.
- [ ] Veris report (Story 2.6) has at least one quotable pass result — screenshot or number ready for the slides.
- [ ] **Bare-exception hygiene**: the two `except Exception: pass` sites in `handler.py` (one in `_extract_booking_info`, one in the main handler) now log to `DebugEvent(event_name='agent_error', ...)` before swallowing — so demo-time failures are visible on the dashboard instead of silent.
- [ ] Demo script locked: opening line, which persona/intent to speak, which "wow moment" comes second (spot-specific vs. buddy), closing. Written down, not improvised.
- [ ] Fallback plan: if live call fails during demo, a pre-recorded 30-second video of a successful run is on the presenter's laptop.

---

## Handoff

Ready for @sm. Kick off with `*create epic-1` and follow the story sequence 1.0 → 1.1 → 1.2 → 1.3 → 1.4 before starting Epic 2. Stories within Epic 2 can be parallelized across two developers once Story 1.0's `DebugEvent` schema is documented — 2.3, 2.4, 2.5, and 2.6 have no hard dependency on 2.1/2.2.
