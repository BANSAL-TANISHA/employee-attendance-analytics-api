# Employee Attendance & Analytics API

FastAPI + MongoDB implementation for the HROne Engineering Assignment (Contract v2.0).

## Run locally (Windows PowerShell)

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:MONGO_URI = "mongodb://localhost:27017"
$env:MONGO_DB = "attendance_db"
uvicorn app.main:app --port 8000
```

MongoDB 6.0+ is required. The application reads `MONGO_URI` and `MONGO_DB` from the environment; configure these for your local MongoDB before starting. Do not point seed scripts or synthetic-data scripts at a database containing valuable data.

Swagger UI: http://127.0.0.1:8000/docs

## Tests

Run the available automated tests from the repository root:

```powershell
pytest -q
```

The sample dataset is intentionally small and does not cover all hidden cases. Verify the edge cases in the assignment: overnight shifts, exact late/overtime boundaries, weekend records, mid-month joiners, employees with no logs, ranking ties, empty date ranges, concurrency, and query plans on a large synthetic dataset.

## Submission notes

All application code must be in `app/main.py`. Submit `app/main.py`, `requirements.txt`, `REVIEW.md`, `DECISIONS.md`, and this `README.md`. Do not commit `.env`, credentials, virtual environments, or database dumps.

**Verification status:** The implementation and review notes should be checked against the exact contract before submission. Passing local tests is not a guarantee of passing hidden tests. In particular, the department summary/trend currently perform some analytics work in Python, and the explain endpoint uses simplified pipelines for analytics; these do not yet satisfy the assignment's full MongoDB-pipeline/explain requirements.
