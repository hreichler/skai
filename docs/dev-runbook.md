# Dev Runbook — start both apps in <2 min

## Bootstrap (once)
```bash
brew install portaudio                 # needed by voicerun-cli pyaudio dep
curl -LsSf https://astral.sh/uv/install.sh | sh && export PATH="$HOME/.local/bin:$PATH"
uv tool install voicerun-cli
vr setup --skip-uv --cursor            # installs Helm + Cursor MCP
vr signin                              # browser OAuth — required for vr debug/push
(cd apps/dashboard && npm install)
```

## Daily loop (two terminals)
```bash
cd apps/agent     && vr debug          # agent debugger (pushes + connects)
cd apps/dashboard && npm run dev       # dashboard → http://localhost:3000
```

## Secrets
Edit repo-root `.env.local` (gitignored). Agent reads it via `apps/agent/config.py`;
dashboard reads it via symlink `apps/dashboard/.env.local → ../../.env.local`.
`DebugEvent` contract lives in `docs/schema.md` — hand-mirror, do not codegen.
