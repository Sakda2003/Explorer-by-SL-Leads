# Screenshotting the app — use Playwright + Edge, not the Browser pane

The Claude Code Browser-pane `computer{action:"screenshot"}` tool fails outright when the
pane isn't visible on screen: *"the Browser pane is not displayed, so the page is not
compositing frames."* Fronting the tab, re-navigating, and retrying don't help — the pane has
to actually be shown in the desktop app. In a session where it isn't, there is no screenshot
path through that tool at all. (This is a harder failure than [[Preview-Pane-Viewport-Unreliable]],
which is about measurements being wrong rather than capture being impossible.)

**Working alternative, installed 2026-08-12:** `playwright` is now in `.venv`, driven through
the already-present Edge browser — `p.chromium.launch(channel='msedge')` — so there is no
~130 MB Chromium download and no extra binary to manage. Playwright is **not** in
`requirements.txt`; it's a local dev convenience, not a runtime dependency.

Recipe that works here:

```python
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(channel='msedge')
    pg = b.new_page(viewport={'width': 1440, 'height': 900}, device_scale_factor=2)
    pg.goto('http://localhost:8000', wait_until='networkidle')
    pg.wait_for_timeout(4000)          # Recharts animates in; shots before this are half-drawn
    pg.screenshot(path='out.png', full_page=True)
```

Notes learned the hard way:

- Start the backend via the `leadlens` config in `.claude/launch.json` (port 8000) — it serves
  the built frontend, so there's no need to run Vite too.
- **Navigating pages:** `get_by_role('button', name='Forecast')` is ambiguous — the sidebar
  button and the Forecast page's own chart toggle both match. Scope it:
  `pg.get_by_role('navigation').get_by_role('button', name=page, exact=True)`.
- **Cropping a region** (a component, or a popover that overflows its trigger): take
  `full_page=True` with a `clip` in *document* coordinates —
  `bounding_box()` is viewport-relative, so add `window.scrollX/scrollY` after
  `scroll_into_view_if_needed()`. Element-only `locator.screenshot()` clips popovers off.
- **Popovers that render empty:** the Budget scenario and Change log popovers show only
  "Select an ad set…" until an ad set is chosen. Fill `#forecast-search` with a real ad set ID
  and press Enter first.
- **Class-name traps when selecting popovers:** `.board-popover` is on the *wrapper* around the
  trigger button, not the menu — grab `.board-menu` (Sort/Columns) or `.filter-menu` (Filter).
  Menus portal to `document.body`, so union the trigger and the menu when you want both.
- **Lead drill-down** opens by clicking inside `.tracking-chart`; not every x-position hits a
  data point, so loop over a few fractions of the chart width until `.lead-drilldown` exists.

Used on 2026-08-12 to build the 47-component UI inventory for the visual redesign — see
[[UI-Component-Inventory]].
