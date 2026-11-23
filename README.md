# Job Hunter

Job Hunter is my personal job-search pipeline for data roles in Germany. Every 30 minutes it collects new
postings, drops the ones I can't get, scores the rest against my skills, writes a tailored CV and cover
letter, and opens the application form already filled in. I read everything and click Submit myself.

Built with Python, PostgreSQL, Docker, GitHub Actions, the Claude API and Playwright.

## How it works

```mermaid
flowchart LR
    S[1. Collect<br/>job boards, company pages,<br/>job-alert emails] --> F[2. Filter<br/>title, location,<br/>German level, years]
    F --> SC[3. Score 0-100<br/>my skills vs. the ad]
    SC --> DB[(4. Store<br/>PostgreSQL + Excel)]
    DB --> T[5. Tailor<br/>CV + cover letter]
    T --> A[6. Fill the form<br/>on the company site]
    A --> ME((7. I review<br/>and click Submit))
```

1. **Collect.** Reads public job feeds and the job-alert emails that LinkedIn, StepStone and XING send me.
2. **Filter.** Drops jobs with the wrong title, outside Germany, needing B2/C1 German or 4+ years.
   Every dropped job keeps its reason, so the filters can be checked.
3. **Score.** Gives each remaining job a match score from 0 to 100 (see below).
4. **Store.** Saves jobs in a database and in `data/applications.xlsx`. Reposts and the same job on two
   sites are recognised, so I never apply twice.
5. **Tailor.** For the best new matches it writes a one-page CV and a cover letter, using only facts
   from my profile.
6. **Fill the form.** `apply.bat` opens the company's application form with my details, CV, letter and
   standard answers filled in.
7. **Submit.** Always by me. The tool never clicks Submit.

## What it never does

- It never submits an application.
- It never logs in to LinkedIn, StepStone, XING or Indeed, and never clicks anything there. It only reads
  their alert emails and public job pages, a few per run.
- It never adds a skill or experience I don't have. Missing tools are named honestly in the letter.

## Job sources

| Source | What it gives | How it is read |
|---|---|---|
| Company career pages (Greenhouse, Lever, Ashby, Personio, SmartRecruiters, Workday) | Jobs at companies I chose, from startups to Bosch and Delivery Hero | Public job-board feeds |
| Amazon (amazon.jobs) | Amazon and AWS jobs in Germany | Amazon's public job search |
| Bundesagentur für Arbeit | Jobs from all of Germany (also what "Make it in Germany" shows) | Public job-search API |
| Arbeitnow | English-speaking jobs in Germany | Public API |
| Data Berlin (databerlin.net) | About 1,000 Berlin data and AI jobs, with the employer's apply link | Public web pages |
| LinkedIn, StepStone, XING, Indeed, Glassdoor | Jobs from my saved job alerts | Alert emails in Gmail (read-only) |

For good matches from LinkedIn or StepStone, Job Hunter also looks for the same job on the company's own
careers site, so I apply there instead of on the job board.

## How a job is scored

| Part | Points | What it checks |
|---|---|---|
| Tools | 60 | How many of the tools in the ad I have. "Airflow, Dagster or similar" counts as a match if I have one. Nice-to-haves count half. |
| Title | 15 | The title is one of my target roles |
| Experience | 10 | The years asked for fit my level |
| Language | 15 | English is the working language |

Rough guide: 85+ apply first, 70-84 good, 55-69 worth a look. Jobs below `min_score` (55) are hidden.
A `?` means the ad text could not be read yet, so the job is not scored.

## Getting started (Windows)

You need Docker Desktop and Python 3.11+.

1. Get the code:
   ```bash
   git clone https://github.com/Nandavits/Job-hunter.git
   cd Job-hunter
   ```
2. Copy the three example files and fill them in:
   - `config/profile.example.yaml` → `config/profile.yaml`: your CV data and form answers
   - `config/config.example.yaml` → `config/config.yaml`: job titles, skills, locations, companies
   - `.env.example` → `.env`: your Claude API key and Gmail app password (both optional)
3. Double-click **`start-jobhunter.bat`**. It starts the database and the scheduler, checks every job
   source and runs the first search. From then on it runs every 30 minutes while the computer is on.
4. In Docker Desktop, turn on *Settings → General → Start Docker Desktop when you sign in*.

Without an API key everything still works. Letters then come from a simple template.

## Applying to a job

Fastest: in `apply.bat` type **T**. Your top 10 jobs (score 75+) open one after another. For each, press
Enter to open the filled-in form, or **s** to skip a job you don't want (it won't come back).

One job at a time:

1. Close Excel.
2. Double-click **`apply.bat`**. It shows your jobs, best first, and tests the Claude key.
3. Type a job's ID and press Enter.
   - Jobs marked `[company site found]`: just press Enter at the link question.
   - Other job-board jobs: open the job, click Apply, and paste the company's link.
4. A browser opens with the form filled in. Check it, tick the consent box and click **Submit**.
5. In the black window, type **y**. The job moves to "applied" in Excel.
6. Optional (`tailoring.linkedin_outreach: true`): the window then shows a LinkedIn people-search link and a
   short note and message for the hiring team. You send it yourself.

Applied jobs disappear from the list. Sites that block automated browsers (SmartRecruiters one-click,
Workday, job boards) open in your normal browser, with your answers and file paths shown to copy.

Fields the tool could not answer are listed under "Needs your answer". Personal questions it has no
answer for get "Prefer not to say".

## The Excel tracker

`data/applications.xlsx` is rewritten after every run, with three sheets: **Applications**, **Open jobs**
and **All jobs** (with the reason each job was skipped). You can change a Status or Notes cell, for
example to mark a job you applied to on LinkedIn, and the next run reads it back.

## Interview preparation

When a company invites me to an interview, I change the job's status to **interview** (in Excel, with
`mark <id> interview`, or with **P** in `apply.bat`). Job Hunter then writes
`Interview_Prep_<Company>.docx` next to the CV and letter for that job:

- a 60-second introduction for this role
- what the employer clearly cares about, taken from the ad
- 8 to 14 likely questions (technical, behavioural, practical) with answers built only from my profile
- honest answers for tools the ad asks for that I have not used
- questions to ask them, and topics to revise before the interview

With a Claude API key the pack is written by Claude; without one, a rule-based pack is built from the
ad and the profile. It never invents experience.

## Running in the cloud (laptop off)

A private copy of the settings can run on GitHub Actions every hour from about 06:00 to 22:00. It collects
and scores jobs, prepares documents and emails a short list of new matches. Applying still happens on the
laptop. Keys are stored as GitHub secrets, never in files.

## Writing style

Cover letters and form answers use short, plain sentences and concrete facts. About 50 typical AI phrases
("excited", "leverage", "perfect fit" and similar) are banned. If one slips in, the letter is rewritten once.
See `jobhunter/style.py`.

## Commands

```bash
python -m jobhunter run                 # one full search
python -m jobhunter list                # open jobs, best first
python -m jobhunter show <id>           # full ad and why it matched
python -m jobhunter fill <id>           # open the pre-filled form (apply.bat does this)
python -m jobhunter queue               # top 10 jobs one after another (apply.bat: T)
python -m jobhunter mark <id> interview --note "Call on Tuesday"   # also writes the interview prep pack
python -m jobhunter prep <id>           # interview preparation pack (apply.bat: P)
python -m jobhunter add --url <link> --company Acme --title "Data Engineer" --description-file ad.txt
python -m jobhunter discover <careers-page-url> --name Acme   # find a company's job board
python -m jobhunter check-sources       # test every job source
python -m jobhunter outreach <id>        # LinkedIn note + message for a job
python -m jobhunter check-key --letters # test the Claude key with one sample letter
python -m jobhunter stats
```

## Project structure

```
jobhunter/
  collectors/     one file per source: ats.py (Greenhouse, Lever, Ashby, Personio, SmartRecruiters,
                  Workday), amazon.py, arbeitsagentur.py, arbeitnow.py, databerlin.py, email_alerts.py
  filters.py      title, location, German level, years, match score
  dedupe.py       same job across reposts and job boards
  career.py       finds the company's own application page for job-board matches
  storage.py      database and status tracking
  excel.py        applications.xlsx, read and written
  tailor.py       CV and letter tailoring with Claude, template fallback
  style.py        writing rules and banned phrases
  outreach.py     LinkedIn note and message drafts (never sent automatically)
  documents.py    one-page CV and letter as .docx and PDF
  autofill.py     fills application forms, never submits
  notify.py       email digest
  pipeline.py     runs the steps in order
  __main__.py     command line
config/           example settings (your real ones are git-ignored)
tests/            144 tests with recorded API answers and sample forms
start-jobhunter.bat, apply.bat   one-click start and apply on Windows
```

## Updates

Changes in `jobhunter/`, `config/` and `.env` are used from the next run. Only changes to
`requirements.txt`, the `Dockerfile` or `docker-compose.yml` need `start-jobhunter.bat` again.

## Tests

```bash
pytest -q
```

They cover every source's parsing, the German-level check, scoring, duplicate detection, the form filler
(including that it never submits), the writing-style check and a full run on recorded data.

## Privacy

`config/profile.yaml`, `config/config.yaml`, `.env`, `data/` and `output/` are git-ignored. Personal data,
keys, the job database and generated documents stay on your computer.

## License

MIT. You can use and change it for your own job search.

## Jako – personal assistant with a team

Jako is a voice and Telegram assistant that runs next to Job Hunter. It uses Claude. Jako is the boss and gives
tasks to a team; each member has its own tools and its own voice:

| Member | Does |
|---|---|
| Iron Man | the laptop: open apps, websites and files, find and read files, screenshots ("what's on my screen?"), volume, battery |
| Black Widow | Gmail and Google Calendar (read-only) |
| Hawkeye | Job Hunter: top jobs, details, mark a job applied / interview / rejected |
| Captain America | reminders, to-do list, planning the day |
| Thor | web search, news, facts, and the weather |

Say **"Hello Jako, wake up"** (or send `/team` on Telegram) and every member reports what they see right now.

Safety:
- Iron Man works through a small helper on Windows (`jako/hands.py`) that only listens on this computer and needs
  a secret key. It stays inside your user folder and keeps out of password and key folders.
- Anything that changes the laptop (run a command, write, move or delete a file, close an app, start a program)
  waits for your "yes". That check is in code: an email, web page or file can't approve it, and the AI can't either.
  Anything else you say cancels it. Deleting moves files to the Recycle Bin.
- Email and calendar are read-only. Jako never sends email and never applies to jobs.

Start it: double-click `start-jako.bat`, open http://localhost:8765 in Chrome, click the circle, allow the
microphone and say "Hello Jako, wake up". Laptop control needs Python on Windows (python.org); the script sets
up the rest.

Phone: create a bot with @BotFather in Telegram, put `TELEGRAM_BOT_TOKEN` in `.env`, restart, send the bot a message
and put the chat id it gives you in `TELEGRAM_CHAT_ID`. The bot only answers that chat. The phone works while the
laptop and Docker are running.

Calendar (optional): Google Calendar → Settings → your calendar → "Secret address in iCal format" →
`GOOGLE_CALENDAR_ICS_URL` in `.env`.
