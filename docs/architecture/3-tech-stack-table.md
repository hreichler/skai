# 3. Tech Stack Table

| Category | Technology | Purpose |
| :--- | :--- | :--- |
| **Agent Language** | Python 3.10+ | Voice agent runtime. Single `async def handler(event, context)` generator — no framework to abstract, the handler *is* the agent. |
| **Dashboard Language** | TypeScript / Next.js | Judge-facing "Live Thinking" visualizer. |
| **Agent SDK** | `primfunctions` (bundled with VoiceRun runtime) | Events (`StartEvent`, `TextEvent`, `StopEvent`, `TimeoutEvent`, `TextToSpeechEvent`, `DebugEvent`), `Context` (session state + completion history), `ToolDefinition` (OpenAI-style function calling). |
| **Harness / CLI** | VoiceRun (`vr`) | Scaffold (`vr init`), debug (`vr debug`), local test (`vr test`), deploy (`vr push` / `vr deploy`), telephony & secret mgmt. |
| **LLM** | Gemini 3 Flash Preview (VoiceRun-managed) | Reasoning + tool calling. `voicerun_managed=True` — no API key or billing owned by us. |
| **STT** | Deepgram Flux (VoiceRun-managed, config-driven) | `sttModel: deepgram-flux` in `.voicerun/values.yaml`. No audio-frame plumbing in app code. |
| **TTS** | VoiceRun-managed | Triggered via `yield TextToSpeechEvent(text=..., voice="kore")`. |
| **Simulation** | Veris AI | Adversarial persona robustness testing (validates PRD Goal #3, "Enterprise Reliability"). |
| **Dashboard Host** | Vercel | Next.js deploy target; subscribes to agent `DebugEvent` stream. |
| **API** | Mariana Tek Admin API (REST) | Downstream system of record for classes, payments, reservations. |
| **Package Mgmt** | `uv` (Python), npm/pnpm (TypeScript) | Agent / dashboard respectively. |
| **Deployment** | Helm (Kubernetes) via VoiceRun | `.voicerun/agent.yaml` chart metadata, `.voicerun/values.yaml` per-env overrides, `.voicerun/templates/deployment.yaml` uses Go template syntax. Staging ↔ prod is a values swap, not a code change. |
