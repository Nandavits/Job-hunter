"""The daily run: collect → filter → store → tailor documents → tracker → digest."""
from __future__ import annotations

import json
import re
import time
import logging
from datetime import date
from pathlib import Path

from . import notify
from .collectors import collect_all
from .config import DATA_DIR, OUTPUT_DIR, Settings
from .dedupe import days_since
from .excel import export_excel, sync_from_excel
from .documents import make_documents
from .filters import filter_jobs, quick_keep
from .storage import Store
from .tailor import tailor

log = logging.getLogger("jobhunter")


def copy_descriptions_from_twins(store: Store) -> int:
    """A job without ad text often also came from another site that had it (e.g. Glassdoor + Data Berlin)."""
    from .dedupe import same_job
    rows = store.list(limit=100_000, include_skipped=True)
    with_text = [r for r in rows if len(r["description"] or "") >= 100]
    copied = 0
    for row in rows:
        if row["status"] not in ("new", "prepared") or len(row["description"] or "") >= 100:
            continue
        twin = next((t for t in with_text if t["uid"] != row["uid"]
                     and same_job(row["company"], row["title"], t["company"], t["title"])), None)
        if twin:
            update = {"description": twin["description"]}
            if twin.get("apply_url") and not row.get("apply_url"):
                update["apply_url"] = twin["apply_url"]
            store.update(row["uid"], **update)
            copied += 1
    if copied:
        log.info("Copied the ad text from another site for %d jobs", copied)
    return copied


def backfill_descriptions(store: Store, limit: int = 60, session=None) -> int:
    """Open jobs saved without ad text (e.g. from LinkedIn alerts): fetch it so they can be scored."""
    from .collectors import description_for
    from .collectors.base import make_session
    copy_descriptions_from_twins(store)
    rows = [r for r in store.list(limit=100_000)
            if r["status"] in ("new", "prepared") and len(r["description"] or "") < 100 and r["url"]
            and "glassdoor." not in r["url"]][:limit]       # Glassdoor pages can't be read; career.py handles them
    if not rows:
        return 0
    session = session or make_session()
    done = 0
    for row in rows:
        extra = {}
        try:
            if "databerlin.net/jobs/" in row["url"]:       # also gives the employer's apply link
                from .collectors import databerlin
                from .collectors.base import get
                time.sleep(1)
                page = get(session, row["url"], headers={"Accept": "text/html"})
                text, apply, company = databerlin.parse_detail(page.content) if page is not None else ("", "", "")
                extra = {k: v for k, v in (("apply_url", apply), ("company", company)) if v}
            else:
                text = description_for(session, row["url"])
        except Exception:
            continue
        if len(text) >= 100:
            store.update(row["uid"], description=text, **extra)
            done += 1
    log.info("Fetched the ad text for %d of %d jobs that had none", done, len(rows))
    return done


def fill_posted_date(job) -> str:
    """Put the posting date in ISO form, or find it in the ad text when the source gave none."""
    from .dates import date_in_ad, normalize_date
    job.posted_at = normalize_date(job.posted_at) or date_in_ad(job.description)
    return job.posted_at


def rescore_open_jobs(store: Store, search: dict) -> int:
    """Recompute the match score of jobs you have not applied to yet (scoring can improve over time)."""
    from .filters import evaluate, location_matches, score_job
    from .models import Job
    changed = 0
    from .filters import include_german_ads
    for row in store.list(limit=100_000, include_skipped=True):
        if row["status"] == "skipped" and include_german_ads(search) and \
                str(row.get("excluded_reason") or "").startswith("needs German"):
            row = {**row, "status": "new"}                 # german_ads: include - give it another look
            store.update(row["uid"], status="new", excluded_reason="")
            changed += 1
        if row["status"] not in ("new", "prepared"):
            continue
        job = Job(source=row["source"], external_id=row["external_id"], company=row["company"] or "",
                  title=row["title"] or "", url=row["url"] or "", location=row["location"] or "",
                  description=row["description"] or "", posted_at=row.get("posted_at") or "",
                  extra={"collected_at": row.get("collected_at") or ""})
        fixes = {}
        posted = fill_posted_date(job)
        if posted and posted != (row.get("posted_at") or ""):
            fixes["posted_at"] = posted
        for field in ("title", "company", "description"):       # repair text saved with the wrong encoding
            value = row.get(field) or ""
            if re.search(r"Ã.|â\x80", value):
                try:
                    fixes[field] = value.encode("cp1252", errors="strict").decode("utf-8")
                except (UnicodeEncodeError, UnicodeDecodeError):
                    try:
                        fixes[field] = value.encode("latin-1").decode("utf-8")
                    except (UnicodeEncodeError, UnicodeDecodeError):
                        pass
        if row["source"] == "databerlin" or "databerlin.net" in (row["url"] or ""):
            from .collectors.databerlin import main_ad_text
            trimmed = main_ad_text(fixes.get("description", row["description"] or ""))
            if trimmed != (row["description"] or ""):
                fixes["description"] = trimmed
        if row["source"] == "databerlin" and re.search(r"\s\d+$", row["company"] or ""):
            fixes["company"] = re.sub(r"(\s+\d+)+$", "", fixes.get("company", row["company"]))
        if fixes:
            store.update(row["uid"], **fixes)
            row = {**row, **fixes}
            job.title, job.company, job.description = row["title"], row["company"], row["description"] or ""
        earlier = store.applied_versions(row)
        if earlier:                                 # same role you already applied to (other site / name spelling)
            store.update(row["uid"], status="skipped", excluded_reason=f"already applied (id {earlier[0]['uid'][:8]})")
            changed += 1
            continue
        where = location_matches(job, search)
        if not where and row["status"] in ("new", "prepared"):
            where = evaluate(job, search).excluded_reason   # ad text may be new: German level, years
        if where:
            store.update(row["uid"], status="skipped", excluded_reason=where)
            changed += 1
            continue
        score = score_job(job, search)
        if score != row["score"]:
            store.update(row["uid"], score=score, matched_skills=",".join(job.matched_skills))
            changed += 1
    return changed


def skip_known_jobs(store: Store, jobs: list[dict], reapply_after_days: int = 180):
    """Drop reposts of roles you already applied to, and second copies of the same role (other job board)."""
    fresh, reposts, duplicates = [], [], []
    for job in jobs:
        matches = store.find_same_job(job["company"], job["title"], job["uid"])
        applied = [m for m in matches if m["status"] in ("applied", "interview", "offer", "rejected")
                   and days_since(m.get("applied_at") or m.get("collected_at") or "") <= reapply_after_days]
        if applied:
            a = applied[0]
            store.update(job["uid"], status="skipped",
                         excluded_reason=f"already applied on {a.get('applied_at') or '?'} (id {a['uid'][:8]}) - repost")
            reposts.append(dict(job, applied_on=a.get("applied_at") or "", status=a["status"]))
            continue
        open_copy = [m for m in matches if m["status"] in ("new", "prepared")]
        if open_copy:
            store.update(job["uid"], status="skipped",
                         excluded_reason=f"same job as {open_copy[0]['uid'][:8]} ({open_copy[0]['source']})")
            duplicates.append(job)
            continue
        fresh.append(job)
    if reposts or duplicates:
        log.info("Skipped %d reposts you already applied to and %d duplicates", len(reposts), len(duplicates))
    return fresh, reposts, duplicates


DOC_VERSION = 2      # raise when CV/letter output improves; older documents are re-made before applying


FULL_LIST_SOURCES = {"greenhouse", "lever", "ashby", "personio", "smartrecruiters", "workday", "databerlin"}


def mark_closed_jobs(store: Store, raw: list) -> int:
    """Open jobs from sources that list every job each run (company boards, Data Berlin): if a job is no
    longer listed, the company has closed it, so it leaves your list instead of leading to a dead link."""
    seen_urls = {j.url for j in raw}
    seen_sources = {j.source for j in raw}
    seen_boards = {(j.source, (j.company or "").lower()) for j in raw}
    closed = 0
    for row in store.list(limit=100_000):
        source = row["source"]
        if row["status"] not in ("new", "prepared") or source not in FULL_LIST_SOURCES or source not in seen_sources:
            continue
        if source != "databerlin" and (source, (row["company"] or "").lower()) not in seen_boards:
            continue                       # that company's board was not read this run: can't tell
        if row["url"] not in seen_urls:
            store.update(row["uid"], status="skipped", excluded_reason="job closed (no longer listed)")
            closed += 1
    if closed:
        log.info("%d jobs were closed by the companies and removed from your open list", closed)
    return closed


def employer_link(session, job: dict) -> str:
    """The company's own application page for a job seen on Data Berlin or a job board ("" if unknown)."""
    from .career import find_career_link, is_job_board
    url = job.get("url") or ""
    if "databerlin.net/jobs/" in url:
        from .collectors import databerlin
        from .collectors.base import get
        page = get(session, url, headers={"Accept": "text/html"})
        return databerlin.parse_detail(page.content)[1] if page is not None else ""
    if is_job_board(url):
        return find_career_link(session, job)[0]
    return ""


def needs_employer_link(job: dict) -> bool:
    from .career import is_job_board
    apply = job.get("apply_url") or ""
    url = job.get("url") or ""
    return ("databerlin.net" in url or is_job_board(url)) and (not apply or "databerlin.net" in apply
                                                               or is_job_board(apply))


def add_databerlin_links(store: Store, session, limit: int = 40) -> int:
    """Data Berlin jobs saved before their employer link was read: read it now."""
    rows = [r for r in store.list(limit=100_000)
            if r["status"] in ("new", "prepared") and "databerlin.net/jobs/" in (r["url"] or "")
            and needs_employer_link(r)][:limit]
    done = 0
    for row in rows:
        try:
            link = employer_link(session, row)
        except Exception:
            continue
        if link:
            store.update(row["uid"], apply_url=link)
            done += 1
    if rows:
        log.info("Found the employer's application link for %d of %d Data Berlin jobs", done, len(rows))
    return done


def prepare_job(settings: Settings, store: Store, job: dict) -> dict:
    """Tailor CV + letter for one stored job and mark it 'prepared'."""
    if not settings.env("ANTHROPIC_API_KEY"):
        log.info("No Claude API key here: %s gets the template cover letter", job.get("company"))
    t = tailor(settings.profile, job, api_key=settings.env("ANTHROPIC_API_KEY"),
               model=settings.env("ANTHROPIC_MODEL", "claude-sonnet-5-5"))
    docs = make_documents(settings.profile, t, job, OUTPUT_DIR,
                          make_pdf=settings.config.get("tailoring", {}).get("make_pdf", True))
    Path(docs["folder"], "tailoring.json").write_text(json.dumps({
        "title": t.title, "summary": t.summary, "letter": t.letter_paragraphs,
        "fit_notes": t.fit_notes, "gaps": t.gaps, "engine": t.engine, "doc_version": DOC_VERSION,
        "ad_skills_you_have_all_on_cv": t.skill_priority,
        "ad_skills_you_lack_not_added": (job.get("extra") or {}).get("missing_skills", []),
    }, indent=2), encoding="utf-8")
    if settings.config.get("tailoring", {}).get("linkedin_outreach", False):
        from .outreach import as_text, draft
        o = draft(settings.profile, job, t.letter_paragraphs, t.skill_priority,
                  api_key=settings.env("ANTHROPIC_API_KEY"), model=settings.env("ANTHROPIC_MODEL", "claude-sonnet-5-5"))
        Path(docs["folder"], "linkedin_message.txt").write_text(as_text(o, job), encoding="utf-8")
        docs["outreach"] = str(Path(docs["folder"], "linkedin_message.txt"))
    store.update(job["uid"], status="prepared",
                 cv_path=docs.get("cv_pdf") or docs["cv_docx"],
                 letter_path=docs.get("letter_pdf") or docs["letter_docx"])
    log.info("Prepared %s – %s (%s)", job["company"], job["title"], t.engine)
    return docs


def check_application_replies(settings: Settings, store: Store) -> list[str]:
    """Rejections and interview invitations in your inbox -> job status (read-only, see inbox.py)."""
    cfg = settings.config.get("application_replies") or {}
    if cfg.get("enabled") is False:
        return []
    alerts = settings.sources.get("email_alerts") or {}
    try:
        from .inbox import check_replies
        return check_replies(store, settings.env("GMAIL_ADDRESS"), settings.env("GMAIL_APP_PASSWORD"),
                             {"imap_host": alerts.get("imap_host", "imap.gmail.com"), **cfg},
                             DATA_DIR / "inbox_seen.json", skip_senders=alerts.get("senders", []))
    except Exception as exc:
        log.warning("Could not read replies to your applications: %s", exc)
        return []


def run(settings: Settings, *, send_digest: bool = True, make_docs: bool = True, session=None) -> dict:
    store = Store(settings.database_url)
    search = settings.search
    excel_path = DATA_DIR / "applications.xlsx"
    sync_from_excel(store, excel_path)   # statuses you changed in Excel (e.g. applied on LinkedIn)
    from . import discovery
    discovery.FILE = DATA_DIR / "discovered-companies.json"
    if not discovery.FILE.exists() and settings.sources.get("discover_companies", True):
        # first run with discovery: learn the careers pages linked from every job seen so far
        discovery.learn(store.list(limit=100_000, include_skipped=True), settings.sources.get("companies"))
    replies = check_application_replies(settings, store)
    try:
        backfill_descriptions(store, session=session)
    except Exception as exc:
        log.warning("Could not fetch missing ad texts: %s", exc)
    rescore_open_jobs(store, search)
    try:
        from .linkcheck import close_closed_ads, close_dead_links
        from .collectors.base import make_session as _ms
        close_dead_links(store, session or _ms())
        close_closed_ads(store, session or _ms())      # "no longer accepting applications" on the ad
    except Exception as exc:
        log.warning("Could not check application links: %s", exc)
    try:
        from .collectors.base import make_session as _make_session
        add_databerlin_links(store, session or _make_session())
    except Exception as exc:
        log.warning("Could not read Data Berlin application links: %s", exc)
    try:   # LinkedIn/StepStone/XING/Indeed matches: find the employer's own application page
        from .career import add_career_links
        from .collectors.base import make_session
        if add_career_links(store, session or make_session(), int(search.get("min_score", 55))):
            rescore_open_jobs(store, search)        # jobs that just got their ad text get a score now
    except Exception as exc:
        log.warning("Could not look up company career pages: %s", exc)

    raw = collect_all(settings, keep=quick_keep(search), session=session)
    try:
        mark_closed_jobs(store, raw)
    except Exception as exc:
        log.warning("Could not check for closed jobs: %s", exc)
    for job in raw:
        fill_posted_date(job)
    kept, dropped = filter_jobs(raw, search)
    # Only remember skipped jobs that were relevant roles (so you can see why they were skipped);
    # the hundreds of unrelated jobs on big career pages are not stored.
    relevant_dropped = [j for j in dropped if not j.excluded_reason.startswith("title not in target")]
    new_uids = set(store.save(kept + relevant_dropped))
    new_jobs = [store.get(j.uid) for j in kept if j.uid in new_uids]
    new_jobs, reposts, duplicates = skip_known_jobs(store, new_jobs, search.get("reapply_after_days", 180))
    log.info("Collected %d · matching %d · new %d", len(raw), len(kept), len(new_jobs))

    prepared: dict[str, dict] = {}
    if make_docs:
        top_n = settings.config.get("tailoring", {}).get("top_n", 5)
        candidates = [j for j in new_jobs if j["source"] != "email" and len(j.get("description") or "") > 100]
        for job in candidates[:top_n]:
            try:
                prepared[job["uid"]] = prepare_job(settings, store, job)
            except Exception as exc:
                log.warning("Could not prepare %s: %s", job["title"], exc)

    interview_packs = []
    if make_docs:
        from .interview import prepare_due
        interview_packs = prepare_due(settings, store, OUTPUT_DIR)   # jobs you moved to 'interview'

    tracker = store.export_tracker(DATA_DIR / "tracker.csv")
    excel_file = export_excel(store, excel_path, search)
    md, page = notify.build_digest(new_jobs, prepared, store.stats(), reposts)
    if replies:
        import html as _html
        md += "\n\n## Replies to your applications\n" + "\n".join(f"- {r}" for r in replies)
        page += "<h3>Replies to your applications</h3><ul>" + "".join(
            f"<li>{_html.escape(r)}</li>" for r in replies) + "</ul>"
    digest_path = DATA_DIR / f"digest-{date.today().isoformat()}.md"
    digest_path.write_text(md, encoding="utf-8")

    sent = {}
    invited = any(": interview (" in r for r in replies)
    if send_digest and (new_jobs or invited):
        cfg = settings.config.get("digest", {})
        if cfg.get("email", True):
            files = [d.get(k) for d in prepared.values() for k in ("cv_pdf", "letter_pdf") if d.get(k)] \
                if cfg.get("attach_documents", False) else []        # off by default: just the job list
            sent["email"] = notify.send_email(settings.env("GMAIL_ADDRESS"), settings.env("GMAIL_APP_PASSWORD"),
                                              ("Job Hunter: INTERVIEW invitation + " if invited else "Job Hunter: ")
                                              + f"{len(new_jobs)} new jobs", md, page, files)
        if cfg.get("telegram"):
            sent["telegram"] = notify.send_telegram(settings.env("TELEGRAM_BOT_TOKEN"),
                                                    settings.env("TELEGRAM_CHAT_ID"), md)

    return {"collected": len(raw), "matching": len(kept), "new": len(new_jobs),
            "prepared": len(prepared), "dropped": len(dropped), "tracker": str(tracker),
            "interview_prep": [str(p) for p in interview_packs], "replies": replies,
            "reposts_already_applied": len(reposts), "duplicates": len(duplicates), "excel": str(excel_file),
            "digest": str(digest_path), "sent": sent,
            "drop_reasons": _reason_counts(dropped)}


def _reason_counts(dropped) -> dict:
    counts: dict[str, int] = {}
    for job in dropped:
        key = job.excluded_reason.split(":")[0].split("'")[0].strip()
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: -kv[1]))
