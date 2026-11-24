# Load test report: Job Hunter Cloud

**Date:** 24 Nov 2026 · **Machine:** 2 CPUs, 7 GB RAM (one small test box running *everything*:
PostgreSQL 16, Redis 7, 2 web processes, 2 queue workers and the load generator itself).
Script: `loadtest/run_load_test.py` · raw numbers: `loadtest/results.json`.

## Result in one line

On a single 2-CPU machine the design handles **5,000 users × 400 jobs (2 million match checks) in 5 minutes**
and serves **~145 dashboard loads per second** with no errors. Projected to 100,000 users, the daily work fits
on a few small servers; the web part needs about one.

## 1. Matching (the queue + workers)

| Users | Jobs | Checks (users × jobs) | Workers | Time | Checks per second | Matches kept |
| --- | --- | --- | --- | --- | --- | --- |
| 5,000 | 400 | 2,000,000 | 2 | 302 s | 6,621 | 219,773 (11%) |

Every user had different skills, places, experience limits and minimum scores. Only jobs that passed a
user's filters were stored (11% here; your real settings keep about 2%).

## 2. Website (people opening their job list at the same moment)

| At the same moment | Before the fix: slowest 5% | Before: errors | After the fix: slowest 5% | After: errors | After: told "busy, retry" |
| --- | --- | --- | --- | --- | --- |
| 10 | 0.1 s | 0 | 0.1 s | 0 | 0 |
| 50 | 0.7 s | 0 | 0.6 s | 0 | 0 |
| 100 | 2.6 s | 1 | 2.6 s | 0 | 0 |
| 200 | **30.8 s** | **102 of 1,600** | **6.4 s** | **0** | 170 of 1,600 |

One request takes 12 ms on its own; the slowdown at 100+ is this 2-CPU box being shared by the database,
the web server and the load generator.

**Bug found and fixed:** at 200 simultaneous users, requests waited 30 s for a database connection and then
failed. Fix: a connection pool sized to the web threads (20 + 20), a 5-second limit on waiting, and a
concurrency limit so extra visitors get an instant "busy, try again in a few seconds" (HTTP 503) instead of
a long hang. Result: no failures, the slowest responses went from 31 s to 6 s, and throughput nearly doubled
(34 → 63 per second).

**Second fix from the numbers:** 11% of all user × job pairs were stored. At 100,000 users that grows
without end, so matches nobody acted on are now deleted after 30 days (jobs you applied to are kept).

## 3. Projection to 100,000 users

Assumptions, stated so they can be challenged: 300 new jobs a day (3× what Job Hunter actually finds for
one person: ~100 a day once the first catch-up is done); 20% of users active on a day, 15 page loads each,
busiest hour 10× the average.

| Part | Need at 100,000 users | Measured capacity per 2 CPUs | What it takes |
| --- | --- | --- | --- |
| Fetching jobs | ~15 sources every 30 min | same for any number of users | 1 small process |
| Matching | 100,000 × 300 = 30 M checks/day (94 s per 30-min run) | 6,621 checks/s → 1.3 h of work per day | 2 workers, with room to spare |
| Website | 300,000 page loads/day; busiest ~35/s | ~145/s with no errors | 1 server (2 for safety) |
| Database rows | ~0.6–3.3 M new matches/day before clean-up | 220,000 rows queried in ~12 ms per page | PostgreSQL with the clean-up; partitions later |

## Honest limits of this test

- One machine: the load generator competed with the server for the same 2 CPUs, so real numbers on separate
  machines would be better, not worse.
- Synthetic users and jobs (realistic text, random skills); the AI step (writing CVs) was not load-tested,
  because each user's AI provider does that work and it runs one job at a time on the queue.
- The projection is arithmetic from measured rates, not a run with 100,000 accounts.
