# 2. High-Level Data Flow
1. **Audio Input**: User speaks -> VoiceRun telephony stream.
2. **STT + Intent Extraction**: VoiceRun-managed Deepgram Flux transcribes audio; Gemini 3 Flash Preview (VoiceRun-managed) extracts intent + params (class, spot, guest name) and drives tool calls. State persists on `Context` across turns.
3. **Logic Gate**: Agent invokes `check_payment_options` tool -> `GET /payment_options/` against MT Sandbox.
4. **Execution**: Agent invokes `create_reservation` tool -> `POST /reservations/` with selected class/spot relationships.
5. **Telemetry**: Agent yields `DebugEvent` (e.g., `booking_progress`, `tool_call_start`, `tool_call_complete`) and `TextToSpeechEvent` back to runtime.
6. **UI Update**: Vercel Next.js dashboard subscribes to the `DebugEvent` stream via WebSocket and renders live working memory + API payloads + per-turn latency.
