# Frontend — ReconcLedge dashboard

React + Vite + TypeScript + Tailwind. See
`../docs/STAGE_6_FRONTEND_PLAN.md` for the full phased build plan,
design rationale, and API-to-UI mapping this is built against — this
README only covers running it locally.

## Status

**All 7 planned phases complete** (scaffold → dashboard → live-run
streaming → full results → audit search → reconciliation tools panel
→ merchant config admin → design polish). Every route in the original
Stage 6 plan is real and functional, verified with a real headless-
browser test (`../scripts/deployment_browser_test.py`) driving actual
user interactions against a real backend, not just checked at compile
time. See `../docs/DECISIONS.md` for the full build narrative,
including every real bug found and fixed along the way.

## Running locally

```
npm install
npm run dev
```

Requires the backend running separately (`uvicorn api.app:app` from
the project root — see the main `README.md`). The dev server proxies
`/health` and `/v1/*` to `http://localhost:8000` by default (see
`vite.config.ts`) — set `VITE_API_BASE_URL` if the backend runs
elsewhere.

## Building

```
npm run build
```

Type-checks (`tsc -b`) then bundles with Vite. Output in `dist/`.
