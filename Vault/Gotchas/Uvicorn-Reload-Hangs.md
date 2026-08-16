# Backend Auto-Reload: use watchfiles CLI, not uvicorn --reload

Set up 2026-08-05, after a stale-backend incident: the Dataset page shipped with new
`/api/dataset/*` routes, but the running server had been started before they existed and
404'd every one of them. The frontend is served off disk from `frontend/dist` so it *did*
pick up the rebuild — new page, old backend, which reads as "the feature is broken."

**`uvicorn --reload` does not work in this environment. Do not use it.** It fails in the
worst possible way: WatchFiles detects the edit and logs
`WARNING: WatchFiles detected changes in 'backend\app.py'. Reloading...`, and then the
worker never respawns — no `Started server process` line ever follows. The old process
keeps serving stale code while looking completely healthy. That is *worse than no reload
at all*, because the log actively claims it reloaded. Reproduced on both ports, via both
`preview_start` and a plain shell launch, so it is not a launcher artifact.

Cause is the venv's interpreter arrangement: `.venv` is built on the codex-runtime Python
(`base_prefix = C:\Users\huths\.cache\codex-runtimes\...`, see `.venv/pyvenv.cfg`), and
uvicorn's Windows reloader respawns the worker through `multiprocessing` spawn, which hangs
across that indirection.

**What works instead** — the `watchfiles` CLI as a supervisor, which manages the subprocess
itself rather than relying on uvicorn's internal respawn. Both entries in
`.claude/launch.json` now use it:

```
-m watchfiles ".venv\Scripts\python.exe -m uvicorn backend.app:app --host 127.0.0.1 --port 8000" backend
```

Two details that are load-bearing:
- **The inner interpreter must be the explicit venv path with BACKSLASHES.** Bare `python`
  resolves to the base interpreter, which has no uvicorn (`No module named uvicorn`), and
  forward slashes fail Windows `CreateProcess` outright (`WinError 2`).
- **The trailing `backend` argument scopes the watch.** Without it the watcher covers the
  whole project, and `data/leadlens.db` (plus its WAL, rewritten on every import) and
  `frontend/dist` (rewritten on every `pnpm build`) would restart the server constantly.

Verified 2026-08-05 in both directions — adding a route made it live in ~1s, removing it
dropped it in ~1s — with a real `Started server process` each time, launched through the
`launch.json` config rather than by hand.

**How to apply:** if backend edits ever stop taking effect, check the log for a
`Reloading...` with no following `Started server process`; that signature means the
supervisor died and the server is serving stale code. Restart it rather than debugging the
code, which is not the problem. Related: [[Stack-and-Build]], [[Dataset-Page]].

## Multiple ports showing different app versions — consolidated back to one, 2026-08-14

Reported symptom: browsing the app on different ports showed different versions of the
same page. Root cause was two independent staleness problems stacking on top of each
other, not a bug in either server:

1. **Port 8000 was a plain `uvicorn` process someone had started by hand** (no `--reload`,
   not the `watchfiles`-wrapped `leadlens` config above), so backend edits silently never
   took effect on it — same failure mode this file already documents, just via a different
   path (a bare manual launch instead of `--reload`'s silent hang).
2. **`frontend/dist` was a day stale.** Port 8000 serves the frontend as a static build
   (`backend/app.py` mounts `frontend/dist` when it exists), which only updates on
   `npm run build` — unlike a Vite dev server (`leadlens-frontend`, :5173), which reflects
   `frontend/src` live. Whenever both a built port and a dev-server port were up at once,
   they were two genuinely different snapshots of the app, not a caching illusion.

**Fixed by:** `npm run build --prefix frontend` (refreshes `frontend/dist`), killing the
stray manual `uvicorn` process, and restarting port 8000 through the `watchfiles`-wrapped
`leadlens` launch.json command above instead of a bare `uvicorn` invocation. Verified the
served JS bundle hash (`index-*.js` in the `/` response) matched the fresh build's output
and a brand-new endpoint (`/api/dataset/row-ids`, added same day) responded — confirming
both halves were current, not just the process being alive.

**Going forward: port 8000 is the one app URL.** `leadlens-verify` (:8010) and
`leadlens-frontend` (:5173) in `launch.json` are dev/verification-only — spin them up for a
one-off check via `preview_start`/Playwright, then stop them, rather than leaving either
running alongside 8000. A left-running dev server is exactly what causes "different port,
different version" again. Backend edits auto-restart on 8000 via `watchfiles`; **frontend
edits do not** — `npm run build --prefix frontend` is a manual step after any
`frontend/src` change intended for that port, there is no watcher rebuilding `dist`.
