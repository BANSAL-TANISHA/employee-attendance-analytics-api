# Starter code review

The original starter was inspected against `openapi.yaml` and `DATA_MODEL.md`. The following concrete defects were found and addressed in `app/main.py`:

| # | Where | What is wrong | How to notice | Fix in this version |
|---|---|---|---|---|
| 1 | `health` | Always returns 200 without checking MongoDB | Stop MongoDB and call `/health` | Ping MongoDB; return 503 on failure |
| 2 | employee creation | Check-then-insert is race-prone; no unique index | Parallel duplicate POSTs | Unique `emp_code` index and catch duplicate-key errors |
| 3 | employee creation | `created_at` is naive datetime and response is not epoch ms | Inspect API response/time zone | Store UTC instant and serialize as epoch milliseconds |
| 4 | employee list | `skip = page * page_size` skips page one; `total` ignores filter; no sort | Request page 1 and department filter | Use `(page-1)*page_size`, filtered count, sorted results |
| 5 | punch-in | Unknown employee dereferenced; timestamp interpreted in local machine zone; missing timestamp falsy handling | Unknown code, UTC/IST environment, `punched_at=0` | Validate employee and epoch-ms timestamp; use UTC/IST conversion correctly |
| 6 | punch-in | No status validation, timestamp range validation, or seconds truncation | Invalid status/timestamp or fractional-second timestamp | Pydantic validation and truncation to seconds |
| 7 | punch-in | Duplicate check then insert races; no unique natural-key index | Concurrent duplicate requests | Unique `(emp_code,date)` index; duplicate key maps to 409 |
| 8 | attendance list | Loads entire collection into Python, no database sort, total counts only after loading | Large dataset / memory use | Database filtering, sorting, count and pagination |
| 9 | attendance list | Returns Mongo `_id` as `id`, which is outside the contract; missing history/default fields may leak | Compare response to schema | Omit internal IDs and serialize exact contract fields |
| 10 | helper `compute_late_minutes` | Uses replacement on the punch timestamp's timezone/date and truncates incorrectly at boundary | Test grace boundary and overnight shift | Calculate shift start in IST on the correct attendance date |
| 11 | helper `compute_work_hours` | Python `round` uses bankers rounding | Half-way rounding case | Decimal `ROUND_HALF_UP` |
| 12 | helper `compute_overtime` | Does not handle overnight shifts and fails to enforce the 30-minute threshold | Overnight shift / 29-minute overtime | Calculate the correct next-day shift end and minimum threshold |
| 13 | missing endpoint | Punch-out not implemented | Call endpoint | Implement atomic conditional update and derived values |
| 14 | missing endpoint | Regularization and audit history not implemented | PATCH a record and inspect history | Validate, recompute derived fields, append changes atomically with compare-and-swap |
| 15 | missing endpoints | Analytics endpoints not implemented | Call analytics paths | Add MongoDB aggregation pipelines |
| 16 | missing endpoint | Explain endpoint not implemented | Call admin explain path | Add explain responses for supported endpoint names |
| 17 | app setup | No startup indexes or environment-aware readiness | Inspect indexes / set env variables | Idempotent startup indexes and environment config |

## Reviewed and retained

- `emp_code` is the business identifier, not MongoDB `_id`.
- Instants are stored as BSON datetimes and exposed as epoch milliseconds.
- The two collections are `employees` and `attendance_logs`.
- Analytics should read stored derived fields, not recompute them from historical punches.

This review is a human-readable record of the defects found in the supplied starter, not a claim that every hidden evaluator case has passed. Run the test suite and inspect all aggregation plans against your MongoDB version before submission.
