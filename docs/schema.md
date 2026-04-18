# `DebugEvent` Contract (VoiceRun Agent → Dashboard)

Single source of truth for the one cross-language event the dashboard consumes
from the agent. Per `docs/epics.md#Build-Constraints` we do **not** codegen;
both sides hand-mirror the shapes below.

First used in Story 1.2 (agent emits). Subscribed-to in Stories 2.3 and 2.4
(dashboard consumes).

---

## Shape

### Python (agent side — `apps/agent/handler.py`, Story 1.2+)

Emitted via `yield DebugEvent(...)` from `primfunctions.events`:

```python
from primfunctions.events import DebugEvent

yield DebugEvent(
    event_name="booking_progress",   # see DebugEventName values below
    event_data={...},                # see per-event payloads below
    direction="out",                 # agent-emitted telemetry
    context={                        # minimal context snapshot
        "turn_id": "...",
        "session_id": "...",
        "timestamp": "..."           # ISO 8601
    },
)
```

| Field        | Type   | Notes                                                         |
| :----------- | :----- | :------------------------------------------------------------ |
| `event_name` | `str`  | One of the `DebugEventName` values below.                      |
| `event_data` | `dict` | Event-specific payload (see sections below).                   |
| `direction`  | `str`  | `"out"` for agent-emitted telemetry. `"in"` reserved.          |
| `context`    | `dict` | Minimal snapshot: `turn_id`, `session_id`, `timestamp` (ISO).  |

### TypeScript (dashboard side — `apps/dashboard/`, Stories 2.3, 2.4)

```ts
export type DebugEventName =
  | "booking_progress"
  | "tool_call_start"        // reserved for Story 2.4
  | "tool_call_complete"     // reserved for Story 2.4
  | "agent_error";

export interface DebugEventContext {
  turn_id: string;
  session_id: string;
  timestamp: string; // ISO 8601
}

export interface DebugEvent<T = unknown> {
  event_name: DebugEventName;
  event_data: T;
  direction: "in" | "out";
  context: DebugEventContext;
}
```

---

## `booking_progress` event_data (Story 1.3 emits; Story 2.3 renders)

The dashboard's **primary subscription target**. Fields are populated
incrementally as the agent learns them during the conversation, so every
field is optional.

```ts
export interface BookingProgressEventData {
  name?: string;
  date?: string;          // "YYYY-MM-DD" or human-friendly, renderer tolerates both
  time?: string;          // "HH:MM" or human-friendly
  phone?: string;
  chosen_class?: string;
  chosen_spot?: string;   // e.g. "Bike #14"
  guest_name?: string;    // set only when a buddy booking is in play (Story 2.2)
}
```

Source for field list: `docs/epics.md#Story-2.3` AC "extracted fields".

---

## `tool_call_start` / `tool_call_complete`

**Reserved for Story 2.4** (mid-stream tool-call emission). Do **not** emit
these in Epic 1 — the dashboard must keep working when they are absent.
Shapes will be defined when Story 2.4 lands.

---

## `agent_error`

```ts
export interface AgentErrorEventData {
  message: string;
  where?: string;         // e.g. "discover_spots"
  recoverable?: boolean;
}
```

Used by the "Spot Unavailable" graceful-failure path (NFR3).

---

## Invariants

- The dashboard subscribes to the agent's `DebugEvent` stream over WebSocket
  (see `docs/architecture/2-high-level-data-flow.md`). It never calls the
  Mariana Tek API directly.
- `MT_ACCESS_TOKEN` must never be embedded in any `DebugEvent` payload.
- Any new `event_name` is added to **both** the Python emit site and the TS
  `DebugEventName` union in the same story — no silent additions.
