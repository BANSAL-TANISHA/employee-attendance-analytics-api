# Employee Attendance & Analytics API

FastAPI + MongoDB implementation for the HROne v2.0 assignment. The HTTP contract is in `openapi.yaml`; stored field types and conventions are in `DATA_MODEL.md`.

## Setup (Windows PowerShell)

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
# Ensure MongoDB 6.0+ is running, then edit .env if needed.
python sample_seed.py
uvicorn app.main:app --port 8000 --reload
```

Open `http://127.0.0.1:8000/docs` for Swagger UI. The app reads `MONGO_URI` and `MONGO_DB` from environment variables; environment values take precedence over `.env`.

## Seed data warning

`sample_seed.py` deletes and reloads both collections in the selected database. Use a disposable local database only. The sample contains nine records and is not sufficient to validate analytics correctness.

## Tests

```powershell
pytest -q
python generate_dataset.py --employees 2000 --records 100000 --reset
```

The synthetic-data generator is for local testing only and does not reproduce the employer's hidden data. Do not run it against a database containing valuable data. It clears the selected database when `--reset` is passed.

## Assignment checklist

- Keep all API application code in `app/main.py`.
- Run boundary, concurrency, analytics and explain tests against MongoDB 6.0+.
- Check `/admin/explain/{endpoint}` output for index scans and no collection scans on the tested query plan.
- Never commit `.env`, credentials, virtual environments or data dumps.

This repository is a working implementation attempt, not a guarantee of passing the employer's hidden tests. In particular, inspect the department-trend aggregation on the exact MongoDB version and test empty date ranges and all-no-log departments before submission.
