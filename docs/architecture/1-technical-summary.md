# 1. Technical Summary
- **Core Harness**: VoiceRun (event-driven voice logic via `primfunctions` SDK; single `async def handler(event, context)` generator).
- **Managed Inference Pipeline**: VoiceRun-managed — Gemini 3 Flash Preview (LLM reasoning + tool calling, `voicerun_managed=True`) and Deepgram Flux (STT, `sttModel: deepgram-flux` in `.voicerun/values.yaml`). TTS is VoiceRun-managed (`TextToSpeechEvent(voice="kore")`).
- **Validation**: Veris AI (simulation & root-cause analysis against adversarial personas).
- **Target API**: Mariana Tek Admin API (REST).
