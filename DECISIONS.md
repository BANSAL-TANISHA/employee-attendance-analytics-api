# Technical decisions

1. **Indexes.** A unique `employees.emp_code` index enforces the HR identifier. A unique `(emp_code, date)` attendance index enforces one record per employee/day and protects concurrent punch-ins. Date/employee and employee/date indexes support date-range lists and employee-month queries; status/late indexes should be retained only when explain plans show they help. Some current date/employee indexes overlap, so I would remove redundant ones after measuring real plans.

2. **Punch-in race.** Both requests may pass an employee lookup, but correctness does not depend on a prior attendance lookup. The unique `(emp_code, date)` index allows one insert. MongoDB accepts one; the other raises `DuplicateKeyError`, which the API maps to HTTP 409.

3. **Ties.** The leaderboard uses MongoDB `$rank` on total late minutes. For totals 100, 80, 80, 60, ranks are 1, 2, 2, 4. The cutoff is applied after ranking, so ties at the limit are all returned; tied rows are ordered by employee code.

4. **Headcount.** Employees are selected by `joined_on <= period_end`, grouped by department, and initialized in the summary even when they have no attendance logs. The current implementation achieves this with Python-side roll-up, but the assignment requires moving the cross-collection roll-up into a MongoDB aggregation pipeline.

5. **100x data.** I would benchmark the actual pipelines and indexes on representative data, remove redundant indexes, and consider precomputed summaries only if measurements justify them. The explain endpoint should explain the same pipeline as the live endpoint; simplified match-only explains are not sufficient.
