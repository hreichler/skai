# skai — Mariana Tek AI Voice Concierge

A polyglot monorepo for a 6-hour hackathon build: voice agent on the VoiceRun
platform + a live dashboard.

## Layout

```
apps/
  agent/       # Python voice agent (VoiceRun `vr init` scaffold, runtime: primfunctions)
  dashboard/   # Next.js App Router (TypeScript) — live "thinking" dashboard
docs/          # PRD, epics, architecture, DebugEvent schema, dev runbook
```

No shared `packages/` — per `docs/epics.md#Build-Constraints`, the single
cross-language contract (`DebugEvent`) is documented in `docs/schema.md` and
hand-mirrored in TypeScript. No codegen.

## Getting started

See [`docs/dev-runbook.md`](docs/dev-runbook.md) for the 2-minute quickstart.
