# Technical decisions

1. **Indexes.** A unique index on `employees.emp_code` enforces employee identity. A unique compound index on `attendance_logs (emp_code, date)` enforces one record per employee/day and makes punch-in races safe. Department/join-date and employee/date indexes support filtering and lookups. Date/employee, status/date and date/late-minutes indexes support attendance lists and analytics. Index usefulness should be confirmed with the explain endpoint on the target MongoDB version; indexes add write cost, so I would not add speculative indexes without query evidence.

2. **Punch-in race.** Both requests may check the employee, but neither relies on a pre-insert attendance lookup for correctness. The unique `(emp_code, date)` index permits only one insert. MongoDB accepts one request; the other receives `DuplicateKeyError`, mapped to HTTP 409.

3. **Ties.** The leaderboard uses `$rank`, not array positions. It ranks by total late minutes, returns every employee whose rank is at most `limit`, and sorts tied rows by employee code. A tie at the cutoff can therefore return more than `limit` rows.

4. **Headcount.** Department summary starts from employees who joined by the end of the month and uses `$lookup` with preserved employees, so an employee with no logs still contributes to headcount. Employees who joined later are excluded.

5. **100x data.** I would measure real query plans and latency, review index selectivity, add load tests, and consider a replica set/read scaling and precomputed monthly summaries if query patterns justify the operational complexity.
