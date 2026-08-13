# Upload page: sticky commit bar

Added 2026-08-13, after Sakda reported "there is no button to say Upload into the model".

**The button was always there** — it just could not be found. `.confirm-bar` held the primary
action as the **last element of the last section** in `App.tsx`, below, in order: the
`import-summary` stats, the `budget-detected` section, and the `table-card` preview table. On a
laptop viewport that put the only action on the page under the fold. Worse,
`.budget-detected-list` is `max-height: 320px; overflow-y: auto`, so scrolling with the pointer
over that list is swallowed by its inner scrollbar — the page reads as having nothing further,
and a cleaned file looks like a dead end.

**Fix:** `.upload-commit-bar`, `position: sticky; bottom: 0`, rendered as a sibling after the
preview sections. Shows the row count, the date range, the spend total (ad-performance only) and
a rejected-row count when non-zero, plus **Discard** and the primary **Import** button.

The button was **removed** from the old inline `.confirm-bar` rather than duplicated — two
identical primary actions on one screen is its own confusion. That element keeps its explanatory
note ("Only rows with Amount spent (USD) are stored…"), which belongs next to the table it
describes.

Why sticky and not fixed: sticky inherits the 1080px `.upload-v2-page` content column, so it
stays clear of the sidebar with no width arithmetic. Nothing between the bar and the body
scrollport creates a scroll container — `.app-shell` is `overflow-x: clip`, which does not — so
`bottom: 0` resolves against the viewport.

**Verified in the browser** (fresh bundle, after a cache-busting reload — the pane had been
holding a stale `index-*.js`): `position: sticky`, `bottom: 0px`, `z-index: 30`, bar bottom 910
against a 910px viewport, visible at `scrollY: 0` without scrolling, facts line reading
"4 ad spend rows ready | 2026-08-10 → 2026-08-11 · $27.36", and
`document.querySelectorAll('.confirm-bar button').length === 0` confirming no duplicate action
was left behind.

**Theme correctness was checked statically, not from the pane.** Reading `getComputedStyle`
after flipping `data-theme` returned the dark values for both — the pane was not compositing
(the screenshot call failed for the same reason), which is
[[Preview-Pane-Stale-Computed-Style]] territory and a reflow does not rescue it. Instead:
the rule contains **no colour literals at all**, and every token it uses (`--surface`, `--line`,
`--text`, `--dim`, `--shadow-ink`) is defined in both the dark and light blocks, so it
re-themes by the same mechanism as every other panel.

Related: [[UI-Component-Inventory]], [[Dual-Theme-Redesign]], [[Screenshotting-The-App]].
