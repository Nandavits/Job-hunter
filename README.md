# Job Hunter 🎯

**A personal job-search pipeline: it finds new data jobs every morning, filters out the ones I can't get, writes a tailored CV and cover letter for the best matches, and pre-fills the application form. I review everything and press Submit myself.**

Built with Python, PostgreSQL, Docker, the Claude API and Playwright.

```mermaid
flowchart LR
    subgraph Sources["Collect (public APIs only)"]
        GH[Greenhouse]
        LV[Lever]
        AB[Ashby]
        PE[Personio]
        BA[Bundesagentur für Arbeit]
        EM[Job-alert emails<br/>LinkedIn · StepStone · Indeed · XING]
    end
    Sources --> F[Filter & score<br/>German level · seniority ·<br/>location · years · skills]
    F --> DB[(PostgreSQL / SQLite<br/>jobs + tracker)]
    DB --> T[Tailor with Claude<br/>facts from profile.yaml only]
    T --> D[CV + cover letter<br/>.docx + PDF, one page]
    DB --> N[Daily digest<br/>email · Telegram]
    D --> A[Pre-fill application form<br/>Playwright, never submits]
    A --> YOU((You review<br/>and click Submit))
```

## Why it exists

Applying in Germany without fluent German means most postings are dead ends, and every application needs a tailored CV and cover letter. Fully automated "auto-apply" bots break LinkedIn's rules, get accounts restricted, and send generic applications that don't get interviews. This project automates everything *except* the final click:

| Step | Automated | You |
|---|---|---|
| Find new jobs from company career pages, the federal job board and your job-alert emails | ✅ | |
| Drop jobs needing C1 German, senior roles, wrong city, too many years | ✅ | |
| Rank by skill match | ✅ | |
| Tailor CV + write cover letter (honest about gaps) | ✅ | Read once |
| Fill name, email, phone, links, uploads, standard answers | ✅ | Answer the rest |
| Submit | ❌ never | ✅ |
| Track status (applied → interview → offer) | ✅ | One command |

## Key design decisions

- **Only official, public, read-only endpoints.** Greenhouse, Lever, Ashby and Personio expose the same JSON/XML feeds their own careers pages use; the Bundesagentur für Arbeit has a documented public API. LinkedIn, XING, StepStone and Indeed are **never** logged into or scraped: the tool only reads the alert emails they already send you (IMAP, read-only).
- **No fabricated experience.** The model only reorders and rephrases facts from `config/profile.yaml`. Its JSON output is validated (unknown bullet indices or skill categories are dropped), and a test asserts that tools from the job ad that aren't in the profile never appear in the CV — only as honest "new to me" lines in the letter.
- **Works without an API key.** A rule-based fallback tailors bullets/skills by keyword relevance and writes a template letter, so the pipeline never breaks.
- **Never submits.** The form filler highlights the submit button and waits for a human. A test opens sample forms in headless Chromium and asserts they were not submitted.
- **Idempotent daily runs.** Jobs are keyed by `sha1(source:company:id)`; re-runs only process new postings.
- **Explainable filtering.** Every skipped job stores its reason (e.g. `needs German C1: "Fluent German and English are required"`), so filters can be tuned.

## Quick start

```bash
git clone https://github.com/Nandavits/job-hunter.git
cd job-hunter
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m playwright install chromium                   # for the form filler

cp config/config.example.yaml config/config.yaml        # search settings + companies
cp config/profile.example.yaml config/profile.yaml      # your CV data
cp .env.example .env                                    # API keys (all optional)

python -m jobhunter run                                 # first run
```

PDFs need LibreOffice (`soffice`) on your PATH; without it you get `.docx` files only.

### Windows: one click

With Docker Desktop running, double-click `start-jobhunter.bat`. It builds the containers, checks every job source (`logs/sources.txt`), runs a search (`logs/first-run.log`) and lists the results.

### Windows: applying

Double-click `apply.bat`. The first run installs Python packages and Chromium into `.venv` (3-5 minutes). It lists your jobs, asks for a job id (and, for LinkedIn/StepStone/XING/Indeed jobs, the company's application link), opens the form pre-filled with your details, CV and cover letter, and waits. **You review and click Submit**; then it asks whether you submitted and marks the job as applied.

### Run it every morning with Docker

```bash
docker compose up -d --build        # PostgreSQL + daily scheduler (08:00 Europe/Berlin)
docker compose logs -f jobhunter
```

If the computer is off or asleep at 08:00, the search runs as soon as it is back on that day (it never runs twice a day). Turn on *Docker Desktop → Settings → General → Start Docker Desktop when you sign in* so it starts by itself.

The form filler opens a visible browser, so run `fill` on your own computer, not in Docker.

## Daily use

```bash
python -m jobhunter list                      # new + prepared jobs, best first
python -m jobhunter show 7972a7a9             # full description, why it matched
python -m jobhunter fill 7972a7a9             # opens the form pre-filled; you submit
python -m jobhunter mark 7972a7a9 interview --note "Call with Head of Data on Tue"
python -m jobhunter stats
```

Found a job on LinkedIn? Paste its description into a text file and add it:

```bash
python -m jobhunter add --url https://www.linkedin.com/jobs/view/123 --company Acme \
       --title "Data Engineer" --description-file acme.txt
python -m jobhunter prepare <id>
```

All commands: `run`, `list`, `show`, `prepare`, `fill`, `mark`, `add`, `export`, `stats`, `discover`, `check-sources`, `schedule`.

## Application tracker (Excel) and reposts

`data/applications.xlsx` is rewritten after every run and every apply, with three sheets: **Applications**
(applied / interview / offer / rejected), **Open jobs** and **All jobs** (including why a job was skipped).
Change a Status or Notes cell in Excel and save, and the next run reads it back. For example, mark a job you
applied to on LinkedIn as `applied`. Only cells you changed are read, so an old copy of the file never undoes newer
updates.

Companies often repost the same role with a new posting id, and the same role arrives from the careers page
and from a LinkedIn alert. Jobs are treated as the same when the company and the title match after removing
"(m/w/d)", "(all genders)", locations and job-board suffixes (seniority must match: Junior ≠ Senior).
A repost of a role you applied to in the last `reapply_after_days` (default 180) is skipped and listed in the
digest; `apply` asks before filling a form for a role you already applied to.

## Adding companies

Open a company's careers page and run:

```bash
python -m jobhunter discover https://www.example.com/careers --name Example
# - {name: Example, ats: greenhouse, slug: example}
```

Paste the line into `sources.companies` in `config/config.yaml`, then check all sources with `python -m jobhunter check-sources`.

## Optional setup

| Feature | What you need |
|---|---|
| AI tailoring | `ANTHROPIC_API_KEY` in `.env` |
| Job-alert emails | Job alerts on LinkedIn/StepStone/Indeed/XING sent to Gmail; a Gmail [App Password](https://myaccount.google.com/apppasswords) created **in that same Gmail account**; `email_alerts.enabled: true`. The plain-text part of each alert is parsed (title / company / city / link); StepStone tracking links are de-duplicated by title + company. |
| Email digest | Same Gmail address + App Password |
| Telegram digest | A bot from @BotFather, your chat id, `digest.telegram: true` |
| PostgreSQL | `DATABASE_URL=postgresql+psycopg://user:pass@host/db` (Docker Compose sets this) |

## Project structure

```
jobhunter/
  collectors/      greenhouse · lever · ashby · personio (ats.py), arbeitsagentur.py, email_alerts.py
  filters.py       German-level detection, seniority, location, years, skill scoring
  storage.py       SQLAlchemy models, dedup, application tracker + CSV export
  dedupe.py        same-role detection across reposts and job boards
  excel.py         applications.xlsx export with two-way status sync
  tailor.py        Claude prompt + output validation, rule-based fallback
  documents.py     python-docx CV + cover letter, auto-shrinks to one page, PDF via LibreOffice
  autofill.py      Playwright form filler (never submits)
  notify.py        daily digest (email / Telegram)
  pipeline.py      collect → filter → store → tailor → digest
  __main__.py      CLI
config/            config.example.yaml, profile.example.yaml (real ones are git-ignored)
tests/             118 tests with recorded API fixtures and sample application forms
```

## Tests

```bash
pytest -q
```

Covers every collector's parsing, the German-level detector (English and German phrasing, "nice to have" exceptions), the full pipeline on fixtures including de-duplication, the AI-output validator, and the "never submits" guarantee.

## Privacy

`config/profile.yaml`, `config/config.yaml`, `.env`, `data/` and `output/` are git-ignored: your CV data, keys, database and generated documents never leave your machine. Only you see the digest.

## License

MIT
