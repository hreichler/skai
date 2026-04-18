# PRD: Mariana Tek AI Voice Concierge (Hackathon Edition)

## 1. Goals and Background Context
- **Zero-UI Booking**: Full Search → Select → Book lifecycle via voice in <60s.
- **Agentic Reasoning**: Prove multi-step tool use (Payment check -> Booking).
- **Enterprise Reliability**: 100% success on "Happy Path" interactions via Veris AI.

## 2. Requirements
- **FR1**: Query `GET /class_sessions/` to narrate available times based on voice.
- **FR2**: Check `GET /payment_options/` before booking to ensure user eligibility.
- **FR3**: Execute `POST /reservations/` with specific `spot` ID relationships.
- **FR4**: Support "Buddy Booking" using the `reserved_for_guest` attribute.
- **FR5**: Provide a visual web dashboard showing live STT/TTS and JSON API logs.
- **NFR1**: Voice response latency < 1.5s via Baseten inference.
- **NFR2**: Use hardcoded "Demo User" Bearer Token for immediate sandbox auth.
- **NFR3**: Gracefully handle "Spot Unavailable" errors from Mariana Tek.

## 3. User Interface Design Goals
- **UX Vision**: High-end SoHo studio front-desk persona—proactive and efficient.
- **Implicit Confirmation**: "Great, I've got you on Bike 12 for 6 PM" vs. asking.
- **Voice States**: Greeting (Intent), Availability (Narrate 2-3 options), Execution (Final Handshake).

## 4. Technical Assumptions
- **Architecture**: Monorepo for shared types between Agent and Dashboard.
- **Service Architecture**: Serverless/Event-Driven via VoiceRun.
- **Testing**: Simulation-based via Veris AI against adversarial personas.
- **Source of Truth**: Mariana Tek API (no local reservation database).

## 5. Epic & Story Structure
### Epic 1: Foundational Voice-to-API Loop
- **Story 1.1**: Initialize VoiceRun + Baseten "Hello World" loop.
- **Story 1.2**: Map "Class Search" intent to `GET /class_sessions/`.
- **Story 1.3**: Execute `POST /reservations/` with hardcoded Auth Token.

### Epic 2: The "Buddy" Concierge (The Wow Factor)
- **Story 2.1**: Map "Bike/Spot" requests to specific Mariana Tek Spot IDs.
- **Story 2.2**: Implement "Book for my friend" logic (Reserved for Guest).
- **Story 2.3**: Develop "Live Thinking" Vercel dashboard for real-time demo.
