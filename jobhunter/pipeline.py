"""The daily run: collect → filter → store → tailor documents → tracker → digest."""
from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path

from . import notify
from .collectors import collect_all
from .config import DATA_DIR, OUTPUT_DIR, Settings
from .documents import make_documents
from .filters import filter_jobs, quick_keep
from .storage import Store
from .tailor import tailor

log = logging.getLogger("jobhunter")


def prepare_job(settings: Settings, store: Store, job: dict) -> dict:
    """Tailor CV + letter for one stored job and mark it 'prepared'."""
    t = tailor(settings.profile, job, api_key=settings.env("ANTHROPIC_API_KEY"),
               model=settings.env("ANTHROPIC_MODEL", "claude-sonnet-5-5"))
    docs = make_documents(settings.profile, t, job, OUTPUT_DIR,
                          make_pdf=settings.config.get("tailoring", {}).get("make_pdf", True))
    Path(docs["folder"], "tailoring.json").write_text(json.dumps({
        "title": t.title, "summary": t.summary, "letter": t.letter_paragraphs,
        "fit_notes": t.fit_notes, "gaps": t.gaps, "engine": t.engine}, indent=2), encoding="utf-8")
    store.update(job["uid"], status="prepared",
                 cv_path=docs.get("cv_pdf") or docs["cv_docx"],
                 letter_path=docs.get("letter_pdf") or docs["letter_docx"])
    log.info("Prepared %s – %s (%s)", job["company"], job["title"], t.engine)
    return docs


def run(settings: Settings, *, send_digest: bool = True, make_docs: bool = True, session=None) -> dict:
    store = Store(settings.database_url)
    search = settings.search

    raw = collect_all(settings, keep=quick_keep(search), session=session)
    kept, dropped = filter_jobs(raw, search)
    # Only remember skipped jobs that were relevant roles (so you can see why they were skipped);
    # the hundreds of unrelated jobs on big career pages are not stored.
    relevant_dropped = [j for j in dropped if not j.excluded_reason.startswith("title not in target")]
    new_uids = set(store.save(kept + relevant_dropped))
    new_jobs = [store.get(j.uid) for j in kept if j.uid in new_uids]
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
    md, page = notify.build_digest(new_jobs, prepared, store.stats())
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
            "digest": str(digest_path), "sent": sent,
            "drop_reasons": _reason_counts(dropped)}


def _reason_counts(dropped) -> dict:
    counts: dict[str, int] = {}
    for job in dropped:
        key = job.excluded_reason.split(":")[0].split("'")[0].strip()
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: -kv[1]))
