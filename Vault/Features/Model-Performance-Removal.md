# Model Performance page removal

Removed 2026-08-12 at the product owner's request. The standalone Model Performance page is
no longer part of the application: it has no page type, navigation item, section mapping,
route branch, implementation, or dedicated styling.

[[Dataset-Page]] is now the canonical portfolio-wide place to inspect the data, variable
dictionary, correlation matrix, and OLS diagnostics. [[Forecast-Page-OLS-Panel]] retains the
compact, scope-specific OLS view beside the forecast chart.

The backend model-run, metric, retrain, realization, and diagnostics endpoints remain intact.
They are not exposed by this UI, but were deliberately kept to avoid changing the model-training
pipeline or breaking any external consumers.
