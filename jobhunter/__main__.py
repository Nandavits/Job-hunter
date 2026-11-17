"""Command line interface:  python -m jobhunter <command>"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

from .collectors import ats
from .collectors.base import make_session
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
    from .pipeline import run
    result = run(settings, send_digest=not args.no_digest, make_docs=not args.no_docs)
    print(json.dumps(result, indent=2, ensure_ascii=False))


def cmd_list(args, settings):
    rows = _store(settings).list(status=args.status, limit=args.limit, include_skipped=args.all)
    if not rows:
        print("No jobs yet. Run: python -m jobhunter run")
    for r in rows:
        reason = f"  ({r['excluded_reason'][:60]})" if r["excluded_reason"] else ""
        print(f"{r['uid'][:8]}  {r['status']:<9} {r['score']:>3}  {r['company'][:22]:<22} {r['title'][:55]}{reason}")


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
    cv, letter_file = local_path(job.get("cv_path")), local_path(job.get("letter_path"))
    if not cv.is_file() or not letter_file.is_file():
        print("No documents yet - preparing CV and cover letter first...")
        prepare_job(settings, store, job)
        job.update(store.get(job["uid"]))
        cv, letter_file = local_path(job["cv_path"]), local_path(job["letter_path"])
    docs = {"cv_pdf" if cv.suffix == ".pdf" else "cv_docx": str(cv),
            "letter_pdf" if letter_file.suffix == ".pdf" else "letter_docx": str(letter_file)}
    letter = json.loads((cv.parent / "tailoring.json").read_text(encoding="utf-8"))["letter"]
    print(f"\nOpening: {job['company']} - {job['title']}\n{job.get('apply_url') or job.get('url')}")
    report = fill(job, settings.profile, docs, letter, headless=args.headless)
    print("\nFilled:", *report.filled, sep="\n  ")
    print("Uploaded:", *report.uploaded, sep="\n  ")
    if report.drafted:
        print("Drafted answers - read and adjust before submitting:", *report.drafted, sep="\n  ")
    if report.needs_you:
        print("Needs your answer:", *report.needs_you, sep="\n  ")
    if input("\nDid you submit the application? [y/N] ").strip().lower() == "y":
        retired = store.mark_applied(job["uid"])
        print("Marked as applied." + (f" Also hid {retired} copy/copies of this job from other sources." if retired else ""))
    _export(store)


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


def cmd_excel(args, settings):
    _export(_store(settings))


def cmd_export(args, settings):
    print(_store(settings).export_tracker(Path(args.path) if args.path else DATA_DIR / "tracker.csv"))


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
    return now.date().isoformat() != last_run and (now.hour, now.minute) >= (hour, minute)


def cmd_schedule(args, settings):
    """Run once a day. If the laptop was off/asleep at the set time, run as soon as it is back on.

    Checks every few minutes instead of sleeping until 08:00, because a long sleep does not
    notice that the computer was suspended.
    """
    from .pipeline import run
    at = args.at or settings.config.get("schedule", {}).get("daily_at", "08:00")
    marker = DATA_DIR / "last_run.txt"
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    log.info("Daily run at %s, or later that day if the computer was off. Ctrl+C to stop.", at)
    while True:
        last_run = marker.read_text().strip() if marker.exists() else ""
        now = datetime.now()
        if due_today(now, at, last_run):
            try:
                settings = load_settings()  # pick up config edits
                log.info("Run finished: %s", run(settings))
            except Exception as exc:  # keep the scheduler alive
                log.exception("Run failed: %s", exc)
            marker.write_text(now.date().isoformat())
        time.sleep(args.check_every * 60)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="jobhunter", description="Find, filter and prepare job applications.")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("run", help="collect jobs, prepare documents, send digest")
    p.add_argument("--no-digest", action="store_true")
    p.add_argument("--no-docs", action="store_true")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("list", help="list stored jobs")
    p.add_argument("--status", choices=STATUSES)
    p.add_argument("--limit", type=int, default=40)
    p.add_argument("--all", action="store_true", help="include skipped jobs")
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

    p = sub.add_parser("mark", help="update application status")
    p.add_argument("id")
    p.add_argument("status", choices=STATUSES)
    p.add_argument("--note")
    p.set_defaults(func=cmd_mark)

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

    p = sub.add_parser("discover", help="find a company's ATS + slug from its careers page")
    p.add_argument("url", help="careers page URL (or a saved HTML file)")
    p.add_argument("--name")
    p.set_defaults(func=cmd_discover)

    sub.add_parser("check-sources", help="test every company in config.yaml").set_defaults(func=cmd_check_sources)

    p = sub.add_parser("schedule", help="run every day at a fixed time")
    p.add_argument("--at", help="HH:MM, default from config.yaml")
    p.add_argument("--check-every", type=int, default=5, help="minutes between checks")
    p.set_defaults(func=cmd_schedule)

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    args.func(args, load_settings())


if __name__ == "__main__":
    main()
