# Stack and Build Setup

Backend: FastAPI (`backend/app.py`, `backend/core.py`), SQLite (`data/leadlens.db`,
WAL mode). Frontend: React + Vite + TypeScript (`frontend/`), served in production
straight out of `frontend/dist` (`backend/app.py` mounts it via `StaticFiles`).

**Building the frontend from Claude's shell:** Node/pnpm are installed but not on
Claude's shell PATH. From Bash:
```
export PATH="/c/Program Files/nodejs:/c/Users/huths/AppData/Roaming/npm:$PATH"
cd frontend && pnpm build
```

**Dual-theme system** (installed 2026-07-23, rebuilt 2026-08-12 — see
[[Dual-Theme-Redesign]]). Until the rebuild the light theme was *dead code*:
`index.html` hardcoded `data-theme="dark"`, there was no toggle, and 531 hardcoded
colour literals below the token block overrode the tokens anyway. It now genuinely
works — toggle in the topbar, persisted to `localStorage['leadlens-theme']`, with a
pre-paint bootstrap in `frontend/index.html`. Chart colours are passed to Recharts as
`var(--token)` strings so they re-theme instantly.

Palette: dark = `#0C0D0F` canvas + brand gold `#C9A86A` + cyan `#4FC3D9` for data;
light = `#F4F5F7` canvas, white cards, gold deepened to `#866B28`. Fonts: Inter Tight
(display/body) and JetBrains Mono (numerals/IDs), **self-hosted** via `@fontsource-variable/*`
imported in `main.tsx` — no font CDN at runtime. Full token table:
`design-system/leadlens-forecasting/MASTER.md`, which is now accurate (it had drifted
badly before the rebuild — it documented DM Sans and `#FFD400`, neither of which existed).

**Do not run an app-wide visual "slop sweep"** (font-size floors, removing the KPI
sparklines/eyebrows/card-header stripes) without asking first — one was applied and
fully reverted the same day (2026-07-28) at the user's request. The dense
micro-typography and hardcoded sparkline squiggles are considered part of the app's
look, not slop. Scoped per-page fixes are fine. (The 2026-08-12 redesign was explicitly
requested and preserved all of these.)

**Git repo since 2026-08-12** (initial commit `e936b85`, branch `main`) — before that there
was no version control at all, so anything older than that commit has no history to
revert to. Remote: <https://github.com/Sakda101/Explorer-by-SL-Leads> — **private**, and it
must stay that way: the code and this vault describe Explorer by SL's ad spend, lead
pipeline and modelling decisions in detail, even though the data itself is excluded.

**Pre-redesign copies of `styles.css`, `App.tsx` and `index.html` (`.backups/`,
suffix `.pre-redesign-2026-08-12`) were permanently deleted 2026-08-19** during a
cache-cleanup pass — they were gitignored and predated the repo, so there is no
git history to recover them from and no other known copy exists. They can no
longer be treated as a fallback for pre-redesign state. Before deleting any
gitignored directory as "cache," check whether a vault note documents a reason
it's being kept on purpose.

**Customer data is excluded from version control and must stay that way.** `.gitignore`
holds back `data/` (the 147 MB SQLite DB) and `Dataset/` (Meta exports plus
`lead_level_dataset.csv`, ~3,000 rows carrying a `customer_name` column) — that is
customer PII, and git history is effectively permanent even in a private repo. Also
excluded: `.env`, `backup.env`, `.claude/settings.local.json`, `Redesign Reference/`
(24 MB of third-party design images), `.backups/`. See [[Access-Control]] for the
encrypted-backup path, which is how the database is actually preserved off-machine.

**`.gitattributes` pins `eol=lf`, and that is load-bearing.** Git on Windows would
otherwise check out `deploy/*.sh` with CRLF, and those scripts are piped straight into
bash on a Linux host (`ssh ... 'sudo bash -s' < deploy/server-setup.sh`), where CRLF
fails with `$'\r': command not found`.

**`package.json` pins every dependency to `"latest"`,** so any `pnpm add` re-resolves
the whole lockfile — the 2026-08-12 font install also moved recharts 3.9.2 → 3.10.1 and
vite 8.1.4 → 8.2.1. Build a baseline *before* installing anything, so a later failure
can be attributed correctly.

See [[Access-Control]] for the Docker/deploy topology (Dockerfile, docker-compose,
Cloudflare Access, backup/restore).
