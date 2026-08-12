# Browser Preview Pane Viewport Is Unreliable

In this project's Browser pane, `resize_window` and `window.innerWidth` disagree with
the width the CSS layout engine actually uses. Observed: at a reported `innerWidth` of
375, the layout still used the desktop CSS rule, and an injected media query at
max-width:1050px did not apply — while a class-selector probe inside the *same* media
query did apply. Resetting to "desktop" (1280) flipped the layout the opposite way.

**Why:** the pane's reported metrics and its actual CSS viewport are out of sync, so
narrow-viewport readings are artifacts.

**How to apply:** verify responsive/breakpoint behavior in a real browser, not this
pane. Don't report a "mobile layout is broken" finding based on pane measurements
alone. Content, DOM structure, colors, contrast, and interaction testing in the pane
remain trustworthy — but see the recalc caveat below before trusting any *measurement*.

## Stale computed style when the pane isn't compositing (2026-08-08)

Second, worse failure mode, found while building the Dataset page's board
([[Dataset-Page]]). When the pane is not displayed, the page **stops compositing frames
and throttles style recalculation** — the tell is `computer{action:"screenshot"}`
failing with "the Browser pane is not displayed, so the page is not compositing frames".

In that state, after a React state update:
- `element.style` / `getAttribute('style')` show the **new** value (the DOM is correct), but
- `getBoundingClientRect()` **and `getComputedStyle()`** both return the **old** value.

`getComputedStyle` normally forces a style flush, so this reads as a genuine rendering
bug and is very easy to misdiagnose. During the board work it was briefly mistaken for a
Chromium `table-layout: fixed` invalidation bug, and a correct implementation was changed
on the strength of a bogus measurement before the real cause surfaced.

**How to apply:** before measuring anything in a non-compositing pane, force a
synchronous flush:

```js
el.style.display = 'none'; void el.offsetHeight; el.style.display = '';
```

Flush the *right* subtree — flushing only a table while reading a node outside it
produced a second false finding (that a theme token wasn't swapping in light mode).
For a page-wide check, flush `document.body`. If a measurement contradicts the DOM's
own `style` attribute, suspect the pane before suspecting the browser.
