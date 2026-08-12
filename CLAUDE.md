# Project Notes

Before deep-diving into git-history-style archaeology, re-deriving why something is
built the way it is, or re-reading large swaths of `backend/core.py` for background —
check [Vault/Home.md](Vault/Home.md) first. It's a maintained Obsidian vault covering
this project's architecture, data pipeline, modeling decisions, features, and known
gotchas. It is cheaper to read than re-deriving the same context from code or session
history, though it can drift — verify any file/line-level claim against current source
before relying on it for anything beyond orientation.

**Always update the vault after making a change** — not just for "big" or non-obvious
work. After any edit to code, data pipeline, config, or docs in this project, update
the relevant note in `Vault/` (or add a new one and link it from `Vault/Home.md`)
before ending the turn. Don't leave that context only in chat history.
