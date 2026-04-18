# 2. Requirements
- **FR1**: Query `GET /class_sessions/` to narrate available times based on voice.
- **FR2**: Check `GET /payment_options/` before booking to ensure user eligibility.
- **FR3**: Execute `POST /reservations/` with specific `spot` ID relationships.
- **FR4**: Support "Buddy Booking" using the `reserved_for_guest` attribute.
- **FR5**: Provide a visual web dashboard showing live STT/TTS and JSON API logs.
- **NFR1**: Voice response latency < 1.5s via VoiceRun's managed inference pipeline (Gemini 3 Flash Preview + Deepgram Flux).
- **NFR2**: Use hardcoded "Demo User" Bearer Token for immediate sandbox auth.
- **NFR3**: Gracefully handle "Spot Unavailable" errors from Mariana Tek.
