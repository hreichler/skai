# Architecture: AI Voice Concierge

## 1. Technical Summary
- **Core Harness**: VoiceRun (Event-driven voice logic).
- **Inference**: Baseten (STT/TTS and Model Hosting).
- **Validation**: Veris AI (Simulation & Root-Cause Analysis).
- **Target API**: Mariana Tek Admin API (REST).

## 2. High-Level Data Flow
1. **Audio Input**: User speaks -> VoiceRun Stream.
2. **Intent Extraction**: Baseten processes audio -> Extracts Intent + Params (Class ID, Spot).
3. **Logic Gate**: Agent calls Mariana Tek `GET /payment_options/`.
4. **Execution**: Agent calls Mariana Tek `POST /reservations/`.
5. **UI Update**: Vercel Dashboard receives event via WebSocket to display JSON payload.

## 3. Tech Stack Table
| Category | Technology | Purpose |
| :--- | :--- | :--- |
| **Language** | TypeScript/Node.js | Primary development language. |
| **Harness** | VoiceRun | Voice agent runtime & CLI. |
| **Inference** | Baseten | Low-latency model execution. |
| **Simulation** | Veris AI | Robustness testing & personas. |
| **Dashboard** | Next.js / Vercel | Visual "Live Thinking" demo. |
| **API** | Mariana Tek | Downstream system of record. |
