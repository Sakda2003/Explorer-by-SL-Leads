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
