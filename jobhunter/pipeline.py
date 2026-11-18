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


def backfill_descriptions(store: Store, limit: int = 60, session=None) -> int:
    """Open jobs saved without ad text (e.g. from LinkedIn alerts): fetch it so they can be scored."""
    from .collectors import description_for
    from .collectors.base import make_session
    rows = [r for r in store.list(limit=100_000)
            if r["status"] in ("new", "prepared") and len(r["description"] or "") < 100 and r["url"]][:limit]
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


def rescore_open_jobs(store: Store, search: dict) -> int:
    """Recompute the match score of jobs you have not applied to yet (scoring can improve over time)."""
    from .filters import evaluate, location_matches, score_job
    from .models import Job
    changed = 0
    for row in store.list(limit=100_000):
        if row["status"] not in ("new", "prepared"):
            continue
        job = Job(source=row["source"], external_id=row["external_id"], company=row["company"] or "",
                  title=row["title"] or "", url=row["url"] or "", location=row["location"] or "",
                  description=row["description"] or "")
        fixes = {}
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
        if not where and row["status"] == "new" and len(job.description) >= 100:
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
        "fit_notes": t.fit_notes, "gaps": t.gaps, "engine": t.engine,
        "ad_skills_you_have_all_on_cv": t.skill_priority,
        "ad_skills_you_lack_not_added": (job.get("extra") or {}).get("missing_skills", []),
    }, indent=2), encoding="utf-8")
    store.update(job["uid"], status="prepared",
                 cv_path=docs.get("cv_pdf") or docs["cv_docx"],
                 letter_path=docs.get("letter_pdf") or docs["letter_docx"])
    log.info("Prepared %s – %s (%s)", job["company"], job["title"], t.engine)
    return docs


def run(settings: Settings, *, send_digest: bool = True, make_docs: bool = True, session=None) -> dict:
    store = Store(settings.database_url)
    search = settings.search
    excel_path = DATA_DIR / "applications.xlsx"
    sync_from_excel(store, excel_path)   # statuses you changed in Excel (e.g. applied on LinkedIn)
    try:
        backfill_descriptions(store, session=session)
    except Exception as exc:
        log.warning("Could not fetch missing ad texts: %s", exc)
    rescore_open_jobs(store, search)
    try:   # LinkedIn/StepStone/XING/Indeed matches: find the employer's own application page
        from .career import add_career_links
        from .collectors.base import make_session
        add_career_links(store, session or make_session(), int(search.get("min_score", 55)))
    except Exception as exc:
        log.warning("Could not look up company career pages: %s", exc)

    raw = collect_all(settings, keep=quick_keep(search), session=session)
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

    tracker = store.export_tracker(DATA_DIR / "tracker.csv")
    excel_file = export_excel(store, excel_path)
    md, page = notify.build_digest(new_jobs, prepared, store.stats(), reposts)
    digest_path = DATA_DIR / f"digest-{date.today().isoformat()}.md"
    digest_path.write_text(md, encoding="utf-8")

    sent = {}
    if send_digest and new_jobs:
        cfg = settings.config.get("digest", {})
        if cfg.get("email", True):
            files = [d.get(k) for d in prepared.values() for k in ("cv_pdf", "letter_pdf") if d.get(k)]
            sent["email"] = notify.send_email(settings.env("GMAIL_ADDRESS"), settings.env("GMAIL_APP_PASSWORD"),
                                              f"Job Hunter: {len(new_jobs)} new jobs", md, page, files)
        if cfg.get("telegram"):
            sent["telegram"] = notify.send_telegram(settings.env("TELEGRAM_BOT_TOKEN"),
                                                    settings.env("TELEGRAM_CHAT_ID"), md)

    return {"collected": len(raw), "matching": len(kept), "new": len(new_jobs),
            "prepared": len(prepared), "dropped": len(dropped), "tracker": str(tracker),
            "reposts_already_applied": len(reposts), "duplicates": len(duplicates), "excel": str(excel_file),
            "digest": str(digest_path), "sent": sent,
            "drop_reasons": _reason_counts(dropped)}


def _reason_counts(dropped) -> dict:
    counts: dict[str, int] = {}
    for job in dropped:
        key = job.excluded_reason.split(":")[0].split("'")[0].strip()
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: -kv[1]))
