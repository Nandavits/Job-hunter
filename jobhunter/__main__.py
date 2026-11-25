"""Command line interface:  python -m jobhunter <command>"""
from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from .collectors import ats
from .collectors.base import get, make_session
from .config import DATA_DIR, OUTPUT_DIR, load_settings
from .filters import evaluate
from .models import Job, html_to_text
from .storage import STATUSES, Store

log = logging.getLogger("jobhunter")


EXCEL = DATA_DIR / "applications.xlsx"


def _store(settings, sync: bool = True) -> Store:
    store = Store(settings.database_url)
    if sync:
        from .excel import sync_from_excel
        sync_from_excel(store, EXCEL)   # pick up statuses changed in Excel
    return store


def _export(store: Store) -> None:
    from .excel import export_excel
    print(f"Excel tracker: {export_excel(store, EXCEL)}")


def _need(store: Store, uid: str) -> dict:
    job = store.get(uid)
    if not job:
        sys.exit(f"No job with id starting '{uid}'. Use `list` to see ids.")
    return job


def cmd_run(args, settings):
    from .config import DATA_DIR, OUTPUT_DIR
    from .pipeline import run
    DATA_DIR.mkdir(parents=True, exist_ok=True)      # a fresh install has no data/ or output/ folder yet
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(DATA_DIR / "last-run.log", mode="w", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S"))
    logging.getLogger().addHandler(handler)          # data/last-run.log: what the latest run did
    result = run(settings, send_digest=not args.no_digest, make_docs=not args.no_docs)
    print(json.dumps(result, indent=2, ensure_ascii=False))


def cmd_list(args, settings):
    if args.status or args.all:
        rows = _store(settings).list(status=args.status, limit=args.limit, include_skipped=args.all)
    else:   # default: only jobs you can still apply to, best first (applied and skipped jobs are hidden)
        rows = [r for r in _store(settings).list(limit=100_000) if r["status"] in ("new", "prepared")]
        rows.sort(key=lambda r: (len(r.get("description") or "") < 100, -(r["score"] or 0)))
        rows = rows[: args.limit]
    if not rows:
        print("No jobs yet. Search first: double-click 2-find-jobs (or start-jobhunter), then come back.\n"
              "(Command line: python -m jobhunter run)")
    elif any(len(r.get("description") or "") < 100 for r in rows):
        print("  ? = not scored yet: the ad text could not be read (often LinkedIn alerts). Open the link to judge.")
    for r in rows:
        reason = f"  ({r['excluded_reason'][:60]})" if r["excluded_reason"] else ""
        unscored = r["status"] in ("new", "prepared") and len(r.get("description") or "") < 100
        score = "  ?" if unscored else f"{r['score']:>3}"
        from .career import is_job_board
        direct = " [company site found]" if r.get("apply_url") and is_job_board(r.get("url") or "") \
            and not is_job_board(r["apply_url"]) else ""
        from .dates import age_text
        posted = age_text(r.get("posted_at") or "")
        posted = f"  [posted {posted}]" if posted else ""
        print(f"{r['uid'][:8]}  {r['status']:<9} {score}  {r['company'][:22]:<22} {r['title'][:55]}{posted}{direct}{reason}")


def cmd_show(args, settings):
    job = _need(_store(settings), args.id)
    for key in ("uid", "status", "score", "company", "title", "location", "posted_at", "source", "url",
                "apply_url", "matched_skills", "excluded_reason", "cv_path", "letter_path", "notes"):
        print(f"{key:>16}: {job.get(key) or ''}")
    print("\n" + (job.get("description") or "")[:1500])


def cmd_prepare(args, settings):
    from .pipeline import prepare_job
    store = _store(settings)
    docs = prepare_job(settings, store, _need(store, args.id))
    print(json.dumps(docs, indent=2))


def local_path(path: str) -> Path:
    """Documents made inside Docker are stored as /app/output/...; find them in this folder's output/."""
    p = Path(path or "")
    if path and not p.exists() and "/output/" in path.replace("\\", "/"):
        candidate = OUTPUT_DIR / path.replace("\\", "/").split("/output/", 1)[1]
        if candidate.exists():
            return candidate
    return p


def _show_outreach(settings, job: dict, folder) -> None:
    """After applying: show the LinkedIn note and message (made now if the job has none yet)."""
    from pathlib import Path
    path = Path(folder) / "linkedin_message.txt"
    if not path.is_file():
        from .outreach import as_text, draft
        letter = []
        if (Path(folder) / "tailoring.json").is_file():
            letter = json.loads((Path(folder) / "tailoring.json").read_text(encoding="utf-8")).get("letter", [])
        o = draft(settings.profile, job, letter, (job.get("matched_skills") or "").split(","),
                  api_key=settings.env("ANTHROPIC_API_KEY"), model=settings.env("ANTHROPIC_MODEL", "claude-sonnet-5-5"))
        path.write_text(as_text(o, job), encoding="utf-8")
    print("\n" + path.read_text(encoding="utf-8"))
    print(f"(Saved in {path})")


def cmd_outreach(args, settings):
    store = _store(settings)
    job = _need(store, args.id)
    folder = local_path(job.get("cv_path")).parent if job.get("cv_path") else None
    if folder is None or not folder.is_dir():
        print("No documents for this job yet. Run: python -m jobhunter prepare " + args.id)
        return
    _show_outreach(settings, job, folder)


def cmd_fill(args, settings):
    from .autofill import fill
    from .pipeline import prepare_job
    store = _store(settings)
    job = _need(store, args.id)
    earlier = store.applied_versions(job)
    if earlier:
        e = earlier[0]
        print(f"\n!! You already applied to this role: {e['company']} - {e['title']}"
              f" on {e.get('applied_at') or '?'} (status: {e['status']}, id {e['uid'][:8]}).")
        if input("Apply again anyway? [y/N] ").strip().lower() != "y":
            store.update(job["uid"], status="skipped", excluded_reason=f"already applied (id {e['uid'][:8]})")
            print("Skipped.")
            _export(store)
            return
    if args.url:
        job["apply_url"] = args.url
        store.update(job["uid"], apply_url=args.url)
    else:
        from .pipeline import employer_link, needs_employer_link
        if needs_employer_link(job):
            print("Looking for the company's own application page...")
            try:
                from .collectors.base import make_session
                link = employer_link(make_session(), job)
            except Exception:
                link = ""
            if link:
                job["apply_url"] = link
                store.update(job["uid"], apply_url=link)
                print(f"Found it: {link}")
            else:
                print("Not found: the job board page opens. Click Apply there, then run apply again with that link.")
    cv, letter_file = local_path(job.get("cv_path")), local_path(job.get("letter_path"))
    from .pipeline import DOC_VERSION
    old_docs = False
    if cv.is_file() and (cv.parent / "tailoring.json").is_file():
        meta = json.loads((cv.parent / "tailoring.json").read_text(encoding="utf-8"))
        old_docs = meta.get("doc_version", 1) < DOC_VERSION or (
            meta.get("engine") == "fallback" and settings.env("ANTHROPIC_API_KEY"))
    if old_docs:
        print("Updating your CV and cover letter to the latest version (cleaner for ATS, written by Claude)...")
    if not cv.is_file() or not letter_file.is_file() or old_docs:
        if not old_docs:
            print("No documents yet - preparing CV and cover letter first...")
        prepare_job(settings, store, job)
        job.update(store.get(job["uid"]))
        cv, letter_file = local_path(job["cv_path"]), local_path(job["letter_path"])
    docs = {"cv_pdf" if cv.suffix == ".pdf" else "cv_docx": str(cv),
            "letter_pdf" if letter_file.suffix == ".pdf" else "letter_docx": str(letter_file)}
    letter = json.loads((cv.parent / "tailoring.json").read_text(encoding="utf-8"))["letter"]
    from .autofill import is_homepage, normalize_url, target_url
    if job.get("apply_url") and is_homepage(job["apply_url"]) and not args.url:
        print(f"\nThis job only links to the company homepage ({normalize_url(job['apply_url'])}).")
        print("Opening the job ad instead. If the form is on the company's careers page, find the job there,")
        print("copy that page's link, and run apply again with it as the 'Application link'.")
    print(f"\nOpening: {job['company']} - {job['title']}\n{target_url(job)}")
    from .autofill import answer_sheet, manual_only
    sheet = cv.parent / "form_answers.txt"
    sheet.write_text(answer_sheet(job, settings.profile), encoding="utf-8")
    from .upload import UPLOAD_DIR, fill_upload_folder, open_folder
    try:
        fill_upload_folder(job, cv, letter_file, sheet.read_text(encoding="utf-8"), settings.profile)
        print(f"\nYour CV, cover letter and answers for this job are in: {UPLOAD_DIR}")
        print("In the site's 'Upload' window, pick that folder once - next time it opens there by itself.")
    except OSError as exc:                             # e.g. a file in _UPLOAD is open in Word
        print(f"\nCould not update the _UPLOAD folder ({exc}). The files are in {cv.parent}")
    if manual_only(target_url(job)):
        # Sites that refuse automated browsers: open the form in your normal browser instead.
        print("\nThis site does not allow automated form filling, so it opens in your normal browser.")
        print(f"Upload your CV and cover letter from the _UPLOAD folder (it opens now): {UPLOAD_DIR}")
        open_folder()
        print("\nYour answers to copy:\n")
        print(sheet.read_text(encoding="utf-8"))
        from .upload import open_in_browser
        open_in_browser(target_url(job))               # Chrome, where you are signed in
        report = None
    else:
        print(f"If a 'verify you are human' check appears, solve it in the window yourself. If it keeps failing,\n"
              f"open the job in your normal browser and copy your answers from: {sheet}")
        try:
            report = fill(job, settings.profile, docs, letter, headless=args.headless)
        except RuntimeError as exc:
            print(f"\n{exc}\nPaste the company's application link next time at 'Application link'.")
            return
        except Exception as exc:                       # e.g. the browser window was closed early
            if "closed" in str(exc).lower():
                print("\nThe browser window closed before Job Hunter finished. If you applied anyway, answer y below.")
            else:                                      # an unusual form: never crash, let the person finish by hand
                log.warning("Form filling stopped: %s", str(exc).splitlines()[0][:200])
                print("\nJob Hunter could not fill this form completely (it is an unusual form)."
                      "\nFinish it in the browser yourself: your CV, cover letter and answers are in"
                      f"\n  {UPLOAD_DIR}"
                      "\nIf you applied, answer y below.")
            report = None
    if report is not None:
        print("\nFilled:", *report.filled, sep="\n  ")
        print("Uploaded:", *report.uploaded, sep="\n  ")
        if report.drafted:
            print("Drafted answers - read and adjust before submitting:", *report.drafted, sep="\n  ")
        if report.needs_you:
            print("Needs your answer:", *report.needs_you, sep="\n  ")
    if input("\nDid you submit the application? [y/N] ").strip().lower() == "y":
        retired = store.mark_applied(job["uid"])
        print("Marked as applied." + (f" Also hid {retired} copy/copies of this job from other sources." if retired else ""))
        if settings.config.get("tailoring", {}).get("linkedin_outreach", False):
            _show_outreach(settings, store.get(job["uid"]), cv.parent)
    _export(store)


def cmd_queue(args, settings):
    """Go through your best open jobs one after another: each form opens filled in, you check and submit."""
    import argparse
    store = _store(settings)
    rows = [r for r in store.list(limit=100_000) if r["status"] in ("new", "prepared")
            and len(r.get("description") or "") >= 100 and (r["score"] or 0) >= args.min_score]
    rows.sort(key=lambda r: -(r["score"] or 0))
    rows = rows[: args.count]
    if not rows:
        print(f"No open jobs with a score of {args.min_score} or more right now.")
        return
    print(f"\nYour top {len(rows)} jobs (score {args.min_score}+), best first.")
    for n, row in enumerate(rows, 1):
        print(f"\n[{n}/{len(rows)}] {row['company']} - {row['title']}  (score {row['score']}, {row['location']})")
        choice = input("Enter = open the form,  s = skip (not interested),  q = stop: ").strip().lower()
        if choice == "q":
            break
        if choice == "s":
            store.update(row["uid"], status="skipped", excluded_reason="not interested (you skipped it)")
            print("Skipped - it won't be shown again.")
            continue
        cmd_fill(argparse.Namespace(id=row["uid"], url=None, headless=False), settings)
    _export(store)
    print("\nDone for now. Run it again any time for the next jobs.")


def cmd_mark(args, settings):
    store = _store(settings)
    job = _need(store, args.id)
    if args.status == "applied":
        store.mark_applied(job["uid"])
    values = {"status": args.status}
    if args.note:
        values["notes"] = ((job.get("notes") or "") + f"\n{datetime.now():%Y-%m-%d}: {args.note}").strip()
    store.update(job["uid"], **values)
    print(f"{job['company']} – {job['title']}: {args.status}")
    if args.status == "interview":
        from .config import OUTPUT_DIR
        from .interview import prepare_interview
        print(f"Interview preparation: {prepare_interview(settings, store, store.get(job['uid']), OUTPUT_DIR)}")
    _export(store)


def cmd_prep(args, settings):
    """Interview preparation pack for one job: questions, answers from your profile, gaps, pitch."""
    from .config import OUTPUT_DIR
    from .interview import prepare_interview
    store = _store(settings)
    job = _need(store, args.id)
    path = prepare_interview(settings, store, job, OUTPUT_DIR, force=args.again)
    print(f"Interview preparation for {job['company']} – {job['title']}:\n  {path}")
    if job["status"] not in ("interview", "offer"):
        store.update(job["uid"], status="interview")
        print("Status set to interview.")
    _export(store)


def cmd_add(args, settings):
    """Add a job you found yourself (e.g. on LinkedIn): paste the description into a text file."""
    description = Path(args.description_file).read_text(encoding="utf-8") if args.description_file else ""
    job = Job(source="manual", external_id=args.url, company=args.company, title=args.title, url=args.url,
              location=args.location, description=html_to_text(description) if "<" in description else description)
    evaluate(job, settings.search)
    store = _store(settings)
    store.save([job])
    if job.excluded_reason:
        print(f"Warning: the filter would skip this job ({job.excluded_reason}). Saved anyway as 'new'.")
        store.update(job.uid, status="new", excluded_reason="")
    print(f"Added {job.uid[:8]} (score {job.score}). Next: python -m jobhunter prepare {job.uid[:8]}")
    _export(store)


def cmd_replies(args, settings):
    from .pipeline import check_application_replies
    store = _store(settings)
    changes = check_application_replies(settings, store)
    print("\n".join(changes) if changes else "No new replies to your applications.")
    _export(store)


def cmd_dashboard(args, settings):
    from .dashboard import serve
    print(f"Job Hunter dashboard: http://127.0.0.1:{args.port}  (close this window to stop it)")
    serve(port=args.port, open_browser=not args.no_browser)


def cmd_excel(args, settings):
    _export(_store(settings))


def cmd_export(args, settings):
    print(_store(settings).export_tracker(Path(args.path) if args.path else DATA_DIR / "tracker.csv"))


def cmd_check_links(args, settings):
    from .collectors.base import make_session
    from .linkcheck import close_dead_links
    close_dead_links(_store(settings), make_session(), dry_run=True)


def cmd_use_cv_search(args, settings):
    """Search for jobs that fit your CV (profile.yaml) instead of the titles written in config.yaml."""
    import shutil
    from .config import CONFIG_DIR
    from .searchplan import switch_on
    cfg = CONFIG_DIR / "config.yaml"
    if not cfg.exists():
        print("config/config.yaml not found.")
        return
    shutil.copy2(cfg, CONFIG_DIR / "config.backup-before-cv-search.yaml")
    if switch_on(cfg):
        print("Done: Job Hunter now searches for jobs that fit your CV (config/profile.yaml).")
        print("Your old settings: config/config.backup-before-cv-search.yaml")
    else:
        print("Could not find the  search:  section in config.yaml - nothing changed.")


def cmd_check_key(args, settings):
    """One tiny request to see whether the Claude API key works (costs a fraction of a cent)."""
    key = settings.env("ANTHROPIC_API_KEY")
    if not key:
        print("Claude API key: not set - letters and open questions use the simple templates.")
        return
    try:
        from .llm import make_client
        make_client(key).messages.create(
            model=settings.env("ANTHROPIC_MODEL", "claude-sonnet-5-5"), max_tokens=5,
            messages=[{"role": "user", "content": "Reply with OK"}])
        via = " (through OpenRouter)" if key.strip().startswith("sk-or-") else ""
        print(f"Claude API key: working{via} - Claude answers open questions on forms.")
    except Exception as exc:
        print(f"Claude API key: NOT working ({type(exc).__name__}: {str(exc)[:160]})")
        return
    if getattr(args, "letters", False):
        from .tailor import tailor
        t = tailor(settings.profile, {"company": "Example GmbH", "title": "Data Engineer", "location": "Berlin",
                                      "description": "We build pipelines with Python, SQL, dbt and Airflow."},
                   api_key=key, model=settings.env("ANTHROPIC_MODEL", "claude-sonnet-5-5"))
        print("Cover letters: written by Claude" if t.engine == "claude"
              else "Cover letters: Claude did not answer correctly - using the template (see messages above)")
        if getattr(args, "show", False):
            from .style import problems
            print("\n".join(t.letter_paragraphs))
            print("Style check:", ", ".join(problems(" ".join(t.letter_paragraphs))) or "no AI phrases found")
            from .outreach import draft
            o = draft(settings.profile, {"company": "Example GmbH", "title": "Data Engineer",
                                         "description": "Python, SQL, dbt and Airflow."}, t.letter_paragraphs,
                      t.skill_priority, api_key=key, model=settings.env("ANTHROPIC_MODEL", "claude-sonnet-5-5"))
            print(f"\nLinkedIn note ({o.engine}, {len(o.note)} chars): {o.note}\n\nLinkedIn message:\n{o.message}")
            from .autofill import claude_answer, claude_choose
            job = {"company": "Example GmbH", "title": "Data Engineer", "description": "Python, SQL, dbt and Airflow."}
            print("\nForm answer test:", claude_answer("Why do you want to work with us?", job, settings.profile,
                                                       t.letter_paragraphs) or "(no answer)")
            print("Dropdown test:", claude_choose("How many years of SQL experience do you have?",
                                                  ["Less than 1 year", "1-2 years", "3-5 years", "More than 5 years"],
                                                  job, settings.profile) or "(no answer)")


def cmd_import_jobs(args, settings):
    from .storage import import_jobs
    n = import_jobs(_store(settings, sync=False), args.file)
    print(f"Copied {n} jobs and applications from {args.file}.")


def cmd_stats(args, settings):
    print(json.dumps(_store(settings).stats(), indent=2))


def cmd_discover(args, settings):
    target = args.url
    text = target
    if target.startswith("http"):
        session = make_session()
        try:
            resp = session.get(target, timeout=20, headers={"Accept": "text/html"})
            text += "\n" + resp.text
        except Exception as exc:
            print(f"Could not load page ({exc}); checking the URL only.")
    elif Path(target).exists():
        text = Path(target).read_text(encoding="utf-8", errors="ignore")
    found = ats.detect_ats(text)
    if not found:
        print("No Greenhouse / Lever / Ashby / Personio board found. This company is not supported automatically;\n"
              "use `python -m jobhunter add` for its jobs.")
    for entry in found:
        extra = ", region: eu" if entry.get("region") == "eu" else ""
        print(f"- {{name: {args.name or 'COMPANY'}, ats: {entry['ats']}, slug: {entry['slug']}{extra}}}")


def cmd_check_sources(args, settings):
    from .collectors import arbeitsagentur, email_alerts
    session = make_session()
    for company in settings.sources.get("companies") or []:
        jobs = ats.fetch_company(session, company)
        print(f"{'OK ' if jobs else '---'} {company['name']:<20} {company['ats']:<10} {len(jobs):>4} jobs")

    an = settings.sources.get("arbeitnow") or {}
    if an.get("enabled"):
        from .collectors import arbeitnow
        jobs = arbeitnow.fetch(session, dict(an, max_pages=1))
        print(f"{'OK ' if jobs else '---'} {'Arbeitnow':<20} {'api':<10} {len(jobs):>4} jobs on page 1")

    amz = settings.sources.get("amazon") or {}
    if amz.get("enabled"):
        from .collectors import amazon
        jobs = amazon.fetch(session, dict(amz, queries=(amz.get("queries") or ["data engineer"])[:1]))
        print(f"{'OK ' if jobs else '---'} {'Amazon':<20} {'web':<10} {len(jobs):>4} jobs")

    dbn = settings.sources.get("databerlin") or {}
    if dbn.get("enabled"):
        from .collectors import databerlin
        resp = get(session, f"{databerlin.BASE}/jobs", headers={"Accept": "text/html"})
        listed = databerlin.parse_list(resp.content) if resp is not None else []
        detail = ("", "", "")
        if listed:
            page = get(session, listed[0].url, headers={"Accept": "text/html"})
            detail = databerlin.parse_detail(page.content) if page is not None else ("", "", "")
        print(f"{'OK ' if listed else '---'} {'Data Berlin':<20} {'web':<10} {len(listed):>4} jobs listed, "
              f"sample job: {len(detail[0])} chars of text, apply link: {'yes' if detail[1] else 'no'}")

    ba = dict(settings.sources.get("arbeitsagentur") or {}, include_remote=False)
    if ba.get("enabled"):
        ba["queries"] = (ba.get("queries") or ["Data Engineer"])[:1]
        jobs = arbeitsagentur.fetch(session, ba, max_details=3)
        with_text = sum(len(j.description) > 100 for j in jobs[:3])
        print(f"{'OK ' if jobs else '---'} {'Arbeitsagentur':<20} {'api':<10} {len(jobs):>4} jobs, "
              f"{with_text}/{min(3, len(jobs))} with full description")

    mail = settings.sources.get("email_alerts") or {}
    address, password = settings.env("GMAIL_ADDRESS"), settings.env("GMAIL_APP_PASSWORD")
    if not mail.get("enabled"):
        print("--- Email alerts         disabled in config.yaml (email_alerts.enabled)")
    elif not (address and password):
        print("--- Email alerts         GMAIL_ADDRESS / GMAIL_APP_PASSWORD missing in .env")
    else:
        try:
            jobs = email_alerts.fetch(dict(mail, days_back=max(mail.get("days_back", 2), 7)), address, password)
            by_site: dict[str, int] = {}
            for job in jobs:
                by_site[job.extra.get("site", "?")] = by_site.get(job.extra.get("site", "?"), 0) + 1
            detail = ", ".join(f"{k} {v}" for k, v in sorted(by_site.items())) or "no job links found"
            print(f"{'OK ' if jobs else '---'} {'Email alerts':<20} {'imap':<10} {len(jobs):>4} jobs ({detail})")
        except Exception as exc:
            print(f"--- Email alerts         login/read failed: {exc}")


def due_today(now: datetime, at: str, last_run: str) -> bool:
    """True if today's run time has passed and today's run hasn't happened yet."""
    hour, minute = map(int, at.split(":"))
    return now.date().isoformat() != last_run[:10] and (now.hour, now.minute) >= (hour, minute)


def due_interval(now: datetime, every_minutes: int, last_run: str) -> bool:
    """True if the last run is at least `every_minutes` old (or there was none)."""
    try:
        last = datetime.fromisoformat(last_run)
    except ValueError:
        return True
    if len(last_run) <= 10:  # only a date was stored (older versions)
        return True
    return (now - last).total_seconds() >= every_minutes * 60 - 30


def cmd_schedule(args, settings):
    """Run on a schedule while the computer is on.

    schedule.every_minutes (e.g. 30): check for new jobs every N minutes.
    otherwise schedule.daily_at: once a day, or as soon as the computer is on after that time.
    """
    cfg = settings.config.get("schedule", {})
    every = args.every or cfg.get("every_minutes")
    at = args.at or cfg.get("daily_at", "08:00")
    marker = DATA_DIR / "last_run.txt"
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    log.info("Checking for new jobs every %s minutes." % every if every else
             "Daily run at %s, or later that day if the computer was off." % at)
    while True:
        last_run = marker.read_text().strip() if marker.exists() else ""
        now = datetime.now()
        if (due_interval(now, int(every), last_run) if every else due_today(now, at, last_run)):
            # A fresh process each time, so updated code and settings are used without restarting.
            result = subprocess.run([sys.executable, "-m", "jobhunter", "run"], check=False)
            if result.returncode:
                log.warning("Run failed with exit code %s", result.returncode)
            marker.write_text(now.isoformat(timespec="minutes"))
        time.sleep(args.check_every * 60)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="jobhunter", description="Find, filter and prepare job applications.")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("outreach", help="show the LinkedIn note and message for a job")
    p.add_argument("id")
    p.set_defaults(func=cmd_outreach)
    p = sub.add_parser("check-links", help="list open jobs whose application page is closed (changes nothing)")
    p.set_defaults(func=cmd_check_links)
    p = sub.add_parser("check-settings", help="check profile.yaml / config.yaml (or any copy) for typing mistakes")
    p.add_argument("files", nargs="*", help="files to check (default: the two files in the config folder)")
    p = sub.add_parser("check-key", help="test the Claude API key")
    p.add_argument("--letters", action="store_true", help="also write one test cover letter (about 3 cents)")
    p.add_argument("--show", action="store_true", help="print the test letter")
    p.set_defaults(func=cmd_check_key)
    p = sub.add_parser("run", help="collect jobs, prepare documents, send digest")
    p.add_argument("--no-digest", action="store_true")
    p.add_argument("--no-docs", action="store_true")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("list", help="list stored jobs")
    p.add_argument("--status", choices=STATUSES)
    p.add_argument("--limit", type=int, default=40)
    p.add_argument("--all", action="store_true", help="include applied and skipped jobs")
    p.set_defaults(func=cmd_list)

    for name, func, helptext in [("show", cmd_show, "show one job"),
                                 ("prepare", cmd_prepare, "make tailored CV + cover letter"),
                                 ("fill", cmd_fill, "open the application form pre-filled (you submit)")]:
        p = sub.add_parser(name, help=helptext)
        p.add_argument("id")
        if name == "fill":
            p.add_argument("--headless", action="store_true", help="for testing only")
            p.add_argument("--url", help="use this application link (e.g. the company page for a LinkedIn job)")
        p.set_defaults(func=func)

    p = sub.add_parser("queue", help="apply to your best open jobs one after another (you submit each)")
    p.add_argument("--count", type=int, default=10)
    p.add_argument("--min-score", type=int, default=75)
    p.set_defaults(func=cmd_queue)

    p = sub.add_parser("mark", help="update application status")
    p.add_argument("id")
    p.add_argument("status", choices=STATUSES)
    p.add_argument("--note")
    p.set_defaults(func=cmd_mark)

    p = sub.add_parser("prep", help="interview preparation pack for a job (questions, answers, gaps, pitch)")
    p.add_argument("id")
    p.add_argument("--again", action="store_true", help="write a fresh pack even if one exists")
    p.set_defaults(func=cmd_prep)

    p = sub.add_parser("add", help="add a job manually (e.g. from LinkedIn)")
    p.add_argument("--url", required=True)
    p.add_argument("--company", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--location", default="Berlin")
    p.add_argument("--description-file", help="text file with the pasted job description")
    p.set_defaults(func=cmd_add)

    sub.add_parser("excel", help="update data/applications.xlsx (reads your Excel edits first)").set_defaults(
        func=cmd_excel)

    p = sub.add_parser("export", help="write the tracker CSV")
    p.add_argument("--path")
    p.set_defaults(func=cmd_export)

    sub.add_parser("stats", help="count jobs per status").set_defaults(func=cmd_stats)
    p = sub.add_parser("import-jobs", help="copy jobs + applications from an older jobs.db file")
    p.add_argument("file")
    p.set_defaults(func=cmd_import_jobs)
    sub.add_parser("use-cv-search", help="search for jobs that fit your CV, not the titles in config.yaml"
                   ).set_defaults(func=cmd_use_cv_search)
    p = sub.add_parser("dashboard", help="your jobs, applications and charts in the browser")
    p.add_argument("--port", type=int, default=8095)
    p.add_argument("--no-browser", action="store_true")
    p.set_defaults(func=cmd_dashboard)
    sub.add_parser("replies", help="read replies to your applications (rejections, interviews) from your inbox"
                   ).set_defaults(func=cmd_replies)

    p = sub.add_parser("discover", help="find a company's ATS + slug from its careers page")
    p.add_argument("url", help="careers page URL (or a saved HTML file)")
    p.add_argument("--name")
    p.set_defaults(func=cmd_discover)

    sub.add_parser("check-sources", help="test every company in config.yaml").set_defaults(func=cmd_check_sources)

    p = sub.add_parser("schedule", help="run every day at a fixed time")
    p.add_argument("--at", help="HH:MM, default from config.yaml")
    p.add_argument("--every", type=int, help="minutes between searches (overrides daily_at)")
    p.add_argument("--check-every", type=int, default=5, help="minutes between checks")
    p.set_defaults(func=cmd_schedule)

    args = parser.parse_args(argv)
    if args.cmd == "check-settings":                 # works even when the settings files are broken
        from .checksettings import run
        from .config import CONFIG_DIR
        sys.exit(run(args.files, CONFIG_DIR))
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    args.func(args, load_settings())


if __name__ == "__main__":
    main()
