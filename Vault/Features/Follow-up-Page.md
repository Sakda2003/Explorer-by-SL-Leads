# Follow-up page

Built 2026-09-04. A sales-work queue over the same `lead_events` records used by Lead
Management. It is available in **Analyze → Follow-up** and includes only leads in Qualified,
Awaiting Document, Awaiting Payment, or the older combined Awaiting Document and Payment stage.

## Data contract

- Pipeline state remains `lead_events.lead_quality`; Converted and Lost updates therefore show
  immediately in Lead Management and disappear from the active Follow-up queue.
- `lead_followups` is a one-to-one extension keyed by `lead_id`. It holds the latest scheduling,
  assignment, contact, document, payment, and outcome details without duplicating the lead.
- `lead_followup_activity` is append-only history for each saved follow-up, including actor,
  prior/new pipeline stage, note, details JSON, and timestamp.
- The original combined `Awaiting Document and Payment` stage remains readable for backward
  compatibility. New workflows can use the separate Awaiting Document and Awaiting Payment
  stages, which were added to `LEAD_QUALITY_OPTIONS` on both backend and frontend.

## API

| Endpoint | Purpose |
| --- | --- |
| `GET /api/follow-up/leads` | Active queue with pagination, search, facets, date presets, filters, and sorting |
| `GET /api/follow-up/leads/{id}` | Full lead details and reverse-chronological activity history |
| `POST /api/follow-up/leads/{id}` | Validate and save an outcome, metadata, pipeline change, and audit activity atomically |

Staff accounts may use the POST endpoint because follow-up is part of normal lead-rating work.
Converted/Lost confirmation is presented by the frontend, while the backend independently
requires a valid outcome and a reason for Lost. Still-deciding submissions require either a
note or a next follow-up date.

## UI behavior

- Date presets: All active, Overdue, Due today, Due this week, Upcoming, and No follow-up date.
- Search plus status, assigned-person, platform, and service filters; sort, grouping, compact
  columns, row selection, bulk movement to document/payment stages, and pagination.
- A right-side detail drawer records the latest contact, method, owner, note, next date, and
  outcome-specific fields. Converted and Lost require confirmation and then leave the queue.
- Activity history is shown in the drawer. Loading, empty, success, and error states are visible.

## Verification

`pnpm build` passes. The backend suite has 258 passing tests (plus 6 subtests). A direct
post-migration call to `get_followup_leads()` succeeds against the local database.
