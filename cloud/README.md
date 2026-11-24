# Job Hunter Cloud: the multi-user version

The desktop Job Hunter (`../jobhunter`) serves one person on one laptop. This folder turns it into
a web service many people can use, designed so the expensive parts do not grow with the number of users.

## The one idea that makes it scale

**Jobs are the same for everyone; only matching and documents are personal.**

| | Desktop version | Cloud version |
| --- | --- | --- |
| Job sites fetched | once per user, every 30 min | **once for all users**, every 30 min |
| Requests to job sites at 10,000 users | ~7 million a day | the same as 1 user (~720 a day) |
| Scoring work per run | all jobs | only **new** jobs per user (a watermark) |
| Stored per user | every job | only jobs that passed that user's filters |
| Heavy work (CVs) | in the main program | on a queue, done by workers you can add |

## Parts

```
browser ──> web (FastAPI) ──> PostgreSQL  (users, shared jobs, per-user matches)
                 │
                 └─> Redis queue ──> worker × N  (match new jobs, write CVs)
scheduler ──> every 30 min: "ingest_and_fan_out" ──> queue
```

- `jhcloud/ingest.py`: fetches every source once using the desktop collectors and stores new jobs.
- `jhcloud/matching.py`: per user, scores only jobs newer than `last_matched_job_id`.
- `jhcloud/tasks.py`: the queued tasks; users are matched in groups of 200 per task.
- `jhcloud/web.py`: sign-up, login, job list with "Why this score", statuses, CV downloads.
- `jhcloud/security.py`: scrypt password hashes, signed session cookies, CSRF tokens, encrypted API keys.

## Security built in

- Passwords hashed with scrypt; the same "wrong email or password" message for unknown accounts.
- Failed logins limited to 10 per IP per 15 minutes (in Redis).
- Signed, HttpOnly, SameSite cookies; a CSRF token on every form.
- Every query includes the logged-in user's id: users cannot see or change each other's jobs (tested).
- Users' AI keys are encrypted (Fernet) and never shown again.
- Security headers (CSP, no framing, no sniffing); HTML in job ads is escaped (tested).
- File downloads only from the user's own folder; Job Hunter never submits applications.

## Run it

Without Docker (SQLite, in-memory queue, sample jobs), to look around:

```
cd cloud
PYTHONPATH=.:.. python -m jhcloud.demo        # http://127.0.0.1:8000  demo@example.com / demo-password-123
```

With Docker (PostgreSQL, Redis, web, worker, scheduler):

```
cd cloud
cp .env.example .env          # fill SECRET_KEY and POSTGRES_PASSWORD
docker compose up -d --build
docker compose up -d --scale worker=4      # more users -> more workers
```

Tests: `cd cloud && python -m pytest -q tests`

## Costs

Building and running on your own computer is free. Each user brings their own Claude or OpenRouter key
(or gets template CVs without one). Free hosting exists for a demo (e.g. Render's free web service +
Neon's free PostgreSQL), but free tiers sleep when idle and cannot carry real traffic.

## Not done yet

- Gmail via OAuth ("Sign in with Google") for reading replies; until then inbox reading stays desktop-only.
- A load test that simulates thousands of users.
- Password reset by email.
