# LeadLens Forecasting

LeadLens is a private, local-first lead forecasting app for cleaned Facebook/CRM traffic exports. It imports Excel or CSV files, merges and deduplicates lead events, retrains per-ad-set forecasting models, and reports 7- and 14-day lead outlooks with prediction ranges and evidence-based confidence scores.

The included sample workbook has been verified against the required `Corrected Traffic` schema. It contains 2,041 lead rows across 11 columns. Campaign, ad set, and ad IDs are forced to text during import; scientific-notation IDs are rejected instead of silently corrupted.

## Quick start on Windows

From this folder:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
cd frontend
pnpm install
pnpm build
cd ..
.\.venv\Scripts\python.exe -m backend.seed_sample
.\.venv\Scripts\python.exe -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). The built React app and API are served by the same local process.

The sample seeding command is optional. You can instead start with an empty database and import the workbook from **Upload Data**.

## Development mode

Run the API and frontend in separate terminals:

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.app:app --reload
```

```powershell
cd frontend
pnpm dev
```

Vite proxies `/api` requests to the local FastAPI server.

## Import contract

Excel files must contain a sheet named `Corrected Traffic`. CSV files use the same column headers:

`Platform`, `Status`, `Created At`, `Updated At`, `Customer Name`, `UTM Campaign`, `UTM Campaign ID`, `UTM Ad Set ID`, `UTM Ad ID`, `FB Ad Title`, `Amount spent (USD)`.

- Every validated row counts as one lead.
- `Created At` is the lead timestamp.
- ID columns are stored as SQLite `TEXT` and read as strings before processing.
- IDs in scientific notation are rejected with a repair instruction.
- Stable event hashes use `Created At`, normalized customer name, ad set ID, ad ID, and status.
- `Amount spent (USD)` is retained as contextual metadata. Because it repeats per lead in the sample, it is never summed as daily spend.
- Raw uploads remain in `data/uploads/` for audit history.

## Forecasting approach

Daily zero-filled lead series are built for each ad set. For sufficiently populated series, rolling backtests compare:

1. Seven-day rolling average.
2. Weekday-aware moving average.
3. A log-link count regression using weekday, 3/7/14-day averages, recent trend, and a time index.

The candidate with the lowest backtest MAE is selected per ad set. Sparse series use a conservative blend of campaign-level pace, overall pace, and the ad set's recent pace. These forecasts are explicitly labeled low confidence.

The confidence score is not a probability. It combines:

- 30% historical data volume.
- 20% recent volatility.
- 30% rolling backtest accuracy.
- 20% relative prediction-interval width.

Sparse forecasts are capped at 40/100. Prediction ranges widen with expected count variance, residual volatility, and sparse history.

## Database schema

SQLite is stored at `data/leadlens.db` by default. Override it with `LEADLENS_DB_PATH`.

- `raw_uploads`: immutable file audit metadata and import counts.
- `lead_events`: normalized, deduplicated lead events with text UTM IDs.
- `upload_lead_links`: many-to-many provenance so deleting one upload does not remove a lead still present in another.
- `daily_ad_set_aggregates`: daily lead counts, ad counts, status mix, and non-summed spend context.
- `model_training_runs`: training history, coverage, accuracy, and notes.
- `forecasts`: versioned 7/14-day results, ranges, confidence, selected model, and drivers.

Deleting an upload removes its provenance links, prunes only orphaned lead events, rebuilds daily aggregates, and triggers a new training run.

## API endpoints

- `POST /api/uploads/preview` — validate and preview `.xlsx` or `.csv`.
- `POST /api/uploads/confirm` — confirm a preview token, merge, deduplicate, and retrain.
- `GET /api/uploads` / `DELETE /api/uploads/{id}` — audit history and governed deletion.
- `GET /api/dashboard/summary` — operational totals and latest model state.
- `GET /api/ad-sets?q=` — searchable ad set IDs.
- `GET /api/forecasts?scope=active|all|selected&ad_set_ids=...` — selected, multi-selected, or active forecasts.
- `GET /api/history?ad_set_id=...` — zero-filled daily history.
- `GET /api/forecasts.csv` — downloadable forecast register.
- `POST /api/models/retrain` / `GET /api/model-runs` — manual retraining and model history.

Interactive API documentation is available at [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs).

## Tests

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

The suite covers schema validation, date parsing, text ID preservation, scientific-notation rejection, deduplication, daily aggregation, both forecast horizons, prediction interval ordering, and sparse-series fallback behavior.

## Deployment path

The current build intentionally uses SQLite for a single internal server. For a multi-user deployment, move the same normalized schema to PostgreSQL, put authentication and TLS in front of FastAPI, store raw uploads in managed object storage, and run retraining as a background job. The React interface and API contract can remain unchanged.
