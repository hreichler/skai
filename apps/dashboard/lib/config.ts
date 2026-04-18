/**
 * Typed dashboard config — Story 1.0.
 *
 * Reads from the SAME `.env.local` at the repo root that `apps/agent/config.py`
 * reads. Next.js automatically inlines `NEXT_PUBLIC_*` vars at build time; all
 * other vars are server-side only (never shipped to the browser).
 *
 * SECURITY invariant (see docs/architecture/2-high-level-data-flow.md):
 *   - `MT_ACCESS_TOKEN` MUST never appear as `NEXT_PUBLIC_*` or be read here.
 *   - The dashboard never calls the MT API directly. It subscribes to the
 *     agent's `DebugEvent` stream (Stories 2.3, 2.4).
 *
 * Throws at module load on any missing required var so misconfig fails fast
 * rather than producing a broken UI at runtime.
 */

function required(name: string, value: string | undefined): string {
  if (!value) {
    throw new Error(`Missing required env var: ${name}`);
  }
  return value;
}

export const config = {
  dashboardUrl: required(
    "NEXT_PUBLIC_DASHBOARD_URL",
    process.env.NEXT_PUBLIC_DASHBOARD_URL,
  ),
} as const;

export type DashboardConfig = typeof config;
