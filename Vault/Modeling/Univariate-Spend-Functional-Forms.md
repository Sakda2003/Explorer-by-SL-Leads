# Univariate Spend Forms: Linear, Quadratic, Log, Square Root

Built 2026-08-19. The spend-only regression is fitted in **four functional forms**, not one,
and the four are ranked against each other by AIC. Reproduces the model comparison the
per-campaign analysis notebooks (`VISA_KR_KHM…ipynb`, `VISA_EU_KHM…`, `VISA_CN&KH_KHM…`,
`VISA_ALL_KHM&FOR…`) were running by hand, one notebook per campaign.

| Form | Formula | Features |
|---|---|---|
| Linear | `Leads ~ Spend` | `spend` |
| Quadratic | `Leads ~ Spend + Spend^2` | `spend`, `spend_sq` |
| Logarithmic | `Leads ~ log(Spend)` | `spend_log` |
| Square root | `Leads ~ sqrt(Spend)` | `spend_sqrt` |

**Why four.** A straight line cannot answer the question a budget decision actually asks —
*is this ad set hitting diminishing returns?* Linear says the next dollar buys exactly what
the last one did, at every spend level, by construction. Log and sqrt bend toward a plateau;
quadratic can bend either way, and a positive squared coefficient means returns are
*accelerating* (the KR | KHM finding in the notebooks). Ranking them is the finding.

## One common row set — the part that is easy to get wrong

`_spend_form_rows` drops days with no spend, and **every form is fitted on the surviving
rows**, not just the two that mathematically need it.

`log(0)` is undefined and `sqrt(0)` pins the transform at its boundary, so those two forms
could not use zero-spend days regardless. The reason the *linear* form gives them up too is
AIC: it is only comparable across models fitted on identical observations. Fitting linear on
70 days and log on 36 and then ranking the two by AIC would be silently invalid — precisely
the sort of thing this card exists to prevent. So the row set is shared.

**Measured cost of the drop (2026-08-19, 30 ad sets): none.**

- 18 ad sets have >= 12 spending days. All 18 get all four forms.
- 12 ad sets have **zero** spend across their whole span. They get no form — and they get no
  linear card today either, for the same reason: `_fit_ols_summary` drops zero-variance
  columns, so a spend model over a constant-zero spend column was never fittable.
- Only 3 ad sets change `n` at all (65→31, 69→31, 57→37). What they give up is a tail of
  zero-spend days carrying 1, 2 and 5 leads respectively.

So "all four forms for every ad set in the webapp" is satisfied exactly as far as it can be:
the coverage is identical to what the linear fit already had. No functional form can rescue
an ad set that never spent money.

## Ranking, and the caveat that has to travel with it

Winner is lowest AIC — which charges quadratic for the extra term it spends. Ranking by R²
instead would hand quadratic the win every single time, since adding a term can never lower
R². `best_caveat` fires when any of the winner's own non-intercept terms sits at p > 0.05:

> Lowest AIC, but spent and spent^2 are not significant at p < 0.05 — treat the shape as
> unconfirmed.

This is the exact trap the notebooks kept hitting. EU | KHM, CN+HK | KHM and ALL | KHM & FOR
all had quadratic edge out linear on AIC while neither of its coefficients was individually
significant, and all three analyses concluded linear was the honest read. Publishing a
winner without that flag would turn a coin-flip into a recommendation. Verified live: ad set
`…210078` wins on quadratic *with* the caveat (p = 0.43 / 0.16), while `…950078` wins on
quadratic *without* it (p = 0.047 / 0.003) — the genuine-curvature case.

## Where it surfaces

- **Comparison table** inside the Spend-only OLS card (`OlsFormComparison`), so it appears on
  both the Forecast page and Dataset — see [[Forecast-Page-OLS-Panel]].
- **Four graphs plus four residual plots** under the spend-vs-leads scatter — one mini plot per form, same dots, its
  own fitted curve, all sharing the parent's axes. Clicking one also draws it on the large
  chart, which a pill picker (plus LOESS) selects too. See [[Spend-Leads-Scatter]].

## Residuals travel with the fit

`_fit_ols_summary` now returns its `residuals` vector (rounded to 4dp), and
`_fit_univariate_spend_forms` returns one `spend_values` axis for all four forms. One axis, not
four: the forms share a row set, which is exactly what makes their residual clouds comparable
point for point, and it removes any chance of a per-form length mismatch pairing a residual with
the wrong day.

Cost: the `/api/ols-summary` payload grew from ~9 KB to ~20 KB. Still nothing against the 1.09 MB
`/api/model-metrics` response this endpoint was split out of. Residuals verified against
statsmodels to within 5e-5, i.e. the rounding.

## Verification

`_fit_univariate_spend_forms` was cross-checked against statsmodels 0.14.5 on the real
portfolio data, using the same formulas the notebooks use (`Leads ~ Spending`,
`~ Spending + I(Spending**2)`, `~ np.log(Spending)`, `~ np.sqrt(Spending)`). R², adjusted R²
and AIC agree to 4 decimal places on all four forms. Four tests cover the fit, the shared row
set, the caveat, and the zero-spend refusal.

**Why:** the shape of the spend→leads relationship is the whole budget question, and one
straight line cannot express it.
**How to apply:** never compare AIC across fits with different `no_observations` — if a form
is ever added that needs a different row set, it needs its own comparison, not a row in this
table. And keep `best_caveat` rendered wherever `best` is: the winner is not a recommendation
on its own.
