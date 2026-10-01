# Follow-up page

Built 2026-09-04. A sales-work queue over the same `lead_events` records used by Lead
Management. It is available in **Analyze → Follow-up**. Intake, Qualified, and Awaiting
Document and Payment leads enter automatically. Not Qualified and Converted leads appear only
when they previously entered Follow-up through one of those three stages.

## Data contract

- Pipeline state remains `lead_events.lead_quality`, so changes show immediately in both views.
- `lead_followups.enrolled_at` records durable queue membership. Directly rating a lead Not
  Qualified or Converted in Lead Management does not enroll it; an enrolled lead remains in
  Follow-up after either of those outcomes for historical visibility.
- `lead_followups` is a one-to-one extension keyed by `lead_id`. It holds the latest scheduling,
  assignment, contact, document, payment, and outcome details without duplicating the lead.
- `lead_followups.selected_service` stores the Follow-up table's inquired-service selection. New
  multi-selections are JSON arrays in the existing text column; older single plain-text values
  remain readable and editable.
- `lead_followup_activity` is append-only history for each saved follow-up, including actor,
  prior/new pipeline stage, note, details JSON, and timestamp.
- Follow-up entry uses the combined `Awaiting Document and Payment` stage exposed by the current
  Lead Management workflow.

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
- The **Inquired Service** column opens a category-first, searchable multi-select containing the
  complete Visa - Cambodia and Visa - Foreigner catalogs. It supports keyboard navigation and
  saves each selection inline.
- Status cells use the same stage palette as the status menu: Intake gold, Not Qualified red,
  Qualified blue, Awaiting Document and Payment amber, Lost dark red, and Converted green.
- A right-side detail drawer records the latest contact, method, owner, note, next date, and
  outcome-specific fields. Enrolled Not Qualified and Converted records remain visible.
- Activity history is shown in the drawer. Loading, empty, success, and error states are visible.

## Verification

`npm run build` passes. The backend suite has 264 passing tests (plus 6 subtests). Follow-up
regression coverage verifies entry-stage enrollment, direct terminal exclusion, and retained
Not Qualified/Converted history.
