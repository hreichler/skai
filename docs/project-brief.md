# Project Brief: Mariana Tek AI Voice Concierge

## 1. Executive Summary
- **Product Concept**: A high-fidelity, autonomous voice receptionist that handles complex gym logistics (spot selection, buddy reservations) via natural conversation.
- **Primary Problem**: Gym members struggle with app friction while on the go; studio owners face high admin overhead for routine booking calls.
- **Target Market**: High-end boutique fitness studios using Mariana Tek (e.g., Barry's, SoulCycle).
- **Key Value Proposition**: A 24/7 human-like voice interface executing multi-step API transactions in seconds.

## 2. Problem Statement
- **Current State**: Existing bots are limited to FAQs. Mariana Tek's power lies in spot-management, but manual navigation is high-friction for active users.
- **Pain Points**: High drop-off for members while commuting; front desk distraction due to routine calls.
- **Why BMad?**: Integration of VoiceRun (harness with managed Gemini 3 Flash + Deepgram Flux inference) and Veris AI (reliability) ensures a hallucination-free, enterprise-ready experience.

## 3. Proposed Solution
- **Core Concept**: An agentic voice interface built on VoiceRun. It executes multi-step transactions: searching bikes -> checking payment -> checkout.
- **Key Differentiators**: 
    - **Agentic Reasoning**: Handles complex requests ("Book next to Sarah").
    - **Buddy Booking**: Specifically manages guest logic and friend-syncing.
- **Success Factors**: Proving robustness via Veris AI simulation sandbox to show judges resilience against API lag and interruptions.

## 4. MVP Scope
- **In Scope**: Inbound voice via VoiceRun, Class Discovery (GET), Standard Booking (POST), Spot Selection (by ID), and Error Handling (Spot Taken).
- **Out of Scope**: Live payment processing (assume existing credits), New User Signup, and Waitlist Management.
- **Success Criteria**: <1.5s latency (VoiceRun managed pipeline); 100% Happy Path success (Veris AI); Clean logs in Mariana Tek Admin.

## 5. Technical Considerations & Constraints
- **Tech Stack**: VoiceRun (with managed Gemini 3 Flash + Deepgram Flux), Mariana Tek Admin API, Veris AI Sandbox.
- **Constraints**: 6-hour build time; Day-of code requirement; 3-minute demo limit.
