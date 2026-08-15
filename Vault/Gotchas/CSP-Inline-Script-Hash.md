# CSP allows the theme script by hash, not by 'unsafe-inline'

The served `frontend/index.html` carries **one inline `<script>`** — the theme-before-paint
stamp that reads `localStorage`/`prefers-color-scheme` and sets `data-theme` before the
stylesheet applies. It *must* stay inline (it has to run before first paint, so it can't be a
module), which collides with a strict Content-Security-Policy: `script-src 'self'` blocks inline
scripts.

The lazy fix — `script-src 'self' 'unsafe-inline'` — is exactly what a security scanner flags,
because it re-permits every inline script including an injected one. Instead,
`backend/security.py` **hashes the inline script body** (`sha256`, base64) and adds
`'sha256-…'` to `script-src`. That allows the one known script and nothing else; `'unsafe-inline'`
never appears on script-src.

Key points:
- The hash is computed at startup from the file **actually served** (`frontend/dist/index.html`,
  falling back to the source), so it stays correct if the theme script is edited — no hardcoded
  digest to drift. `configure_csp()` is called once in `app.py` after the dist path is known.
- The hash is over the **exact bytes between the tags**. If CSP ever blocks it (hash mismatch),
  the failure is graceful: the inline script simply doesn't run, so the app falls back to a brief
  theme flash — it does not break. React still renders normally.
- `style-src` **keeps `'unsafe-inline'`** deliberately: React and Recharts set element `style`
  attributes and `@fontsource` injects `<style>` at runtime. That's not an injection sink here —
  the app renders no user-supplied HTML — and there's no practical way to hash runtime-generated
  styles.

If you add another inline `<script>` to `index.html`, nothing needs doing: the extractor hashes
every inline script it finds. If you move the theme logic into a module, you can drop the hash and
tighten to a bare `script-src 'self'`.

Related: [[Access-Control]], [[Dual-Theme-Redesign]].
