"""Job Hunter dashboard: your jobs, applications and charts in the browser.

    python -m jobhunter dashboard        ->  http://127.0.0.1:8095

Runs on your own computer only (127.0.0.1) and reads the same database as Job Hunter.
Nothing is ever submitted: "Apply" opens the application in a browser and fills it; you click Submit.
"""
from __future__ import annotations

from urllib.parse import quote

import datetime as dt
import logging
import secrets
import subprocess
import sys
import threading
from collections import Counter
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .config import OUTPUT_DIR, ROOT, Settings, load_settings
from .dates import age_days, age_text
from .excel import fit_summary
from .filters import COUNTRY_PLACES, _also_country
from .storage import STATUSES, Store

log = logging.getLogger("jobhunter")
VERSION = "25 Nov 2026"      # shown top right: tells you the update really arrived
OPEN = ("new", "prepared")
APPLIED = ("applied", "interview", "offer", "rejected")
GERMAN_PLACES = ("germany", "deutschland", "berlin", "munich", "münchen", "hamburg", "cologne", "köln", "frankfurt",
                 "stuttgart", "düsseldorf", "leipzig", "dresden", "hannover", "nürnberg", "nuremberg", "bremen",
                 "essen", "dortmund", "bonn", "mannheim", "karlsruhe", "heidelberg", "münster", "leverkusen")


AGE_CHOICES = {"1": 1, "2": 2, "7": 7, "14": 14, "all": 10**6}
AGE_LABELS = {"1": "Last 24 hours", "2": "Last 48 hours", "7": "Last 7 days", "14": "Last 14 days", "all": "Any date"}


def job_age(row: dict) -> int | None:
    """Days since the job was posted; the day Job Hunter found it when the ad gives no date."""
    days = age_days(str(row.get("posted_at") or ""))
    return days if days is not None else age_days(str(row.get("collected_at") or "")[:10])


def country_of(location: str, cfg: dict) -> str:
    loc = (location or "").lower()
    if not loc:
        return "Germany"                         # the job agency and most German boards leave it out
    for country in COUNTRY_PLACES:
        if _also_country(loc, {"also_countries": [country]}):
            if country == "united kingdom":
                return "UK"
            if country not in ("usa", "uae"):
                return country.title()
    if any(p in loc for p in GERMAN_PLACES):
        return "Germany"
    if "remote" in loc:
        return "Remote"
    return "Germany"


def web_link(url: str) -> str:
    """Links from the job agency come without https:// ("www.allianzdeutschland.de"); make them openable."""
    url = (url or "").strip()
    if not url or url.startswith(("http://", "https://")):
        return url
    return "https://" + url.lstrip("/")


def bar_chart(items: list[tuple[str, float]], unit: str = "", width: int = 640) -> str:
    """A small horizontal bar chart as SVG (no JavaScript, no internet needed)."""
    if not items:
        return '<p class="muted">No data yet.</p>'
    top = max(v for _, v in items) or 1
    row, label_w = 26, 170
    h = row * len(items) + 8
    bars = []
    for i, (label, value) in enumerate(items):
        y = 4 + i * row
        w = (width - label_w - 70) * value / top
        bars.append(f'<text x="{label_w - 8}" y="{y + 16}" text-anchor="end" class="lbl">{_esc(label)}</text>'
                    f'<rect x="{label_w}" y="{y + 3}" width="{max(w, 1):.0f}" height="17" rx="4" class="bar"/>'
                    f'<text x="{label_w + w + 6:.0f}" y="{y + 16}" class="val">{value:g}{unit}</text>')
    return f'<svg viewBox="0 0 {width} {h}" class="chart" role="img">{"".join(bars)}</svg>'


def _esc(text: str) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;"))


def week_start(day: str) -> str:
    d = dt.date.fromisoformat(day[:10])
    return (d - dt.timedelta(days=d.weekday())).isoformat()


def charts_data(rows: list[dict], cfg: dict, today: dt.date | None = None) -> dict:
    today = today or dt.date.today()
    weeks = [(today - dt.timedelta(days=today.weekday()) - dt.timedelta(weeks=i)).isoformat() for i in range(7, -1, -1)]
    applied = [r for r in rows if r["status"] in APPLIED and r.get("applied_at")]
    per_week = Counter(week_start(r["applied_at"]) for r in applied)
    replies = [r for r in applied if r["status"] in ("interview", "offer", "rejected")]
    interviews = [r for r in applied if r["status"] in ("interview", "offer")]
    open_jobs = [r for r in rows if r["status"] in OPEN]
    recent = [r for r in rows if (age_days(str(r.get("collected_at") or "")[:10]) or 0) <= 30]
    return {
        "per_week": [(f"week of {w[5:]}", per_week.get(w, 0)) for w in weeks],
        "applied": len(applied), "replies": len(replies), "interviews": len(interviews),
        "reply_rate": round(100 * len(replies) / len(applied)) if applied else 0,
        "interview_rate": round(100 * len(interviews) / len(applied)) if applied else 0,
        "by_source": Counter(r["source"] for r in recent).most_common(10),
        "by_country": Counter(country_of(r.get("location"), cfg) for r in open_jobs).most_common(),
    }


def local_file(raw: str) -> Path | None:
    """Documents made inside Docker are stored as /app/output/...; find them in this folder's output/."""
    if not raw:
        return None
    p = Path(raw)
    if not p.exists() and "/output/" in raw.replace("\\", "/"):
        p = OUTPUT_DIR / raw.replace("\\", "/").split("/output/", 1)[1]
    p = p.resolve()
    return p if p.is_file() and OUTPUT_DIR.resolve() in p.parents else None    # only files Job Hunter made


def manual_kit(job: dict, settings) -> dict:
    """What you paste into a form by hand: your answers one by one, and the cover letter text."""
    import json
    from .autofill import ANSWER_SHEET_KEYS, letter_plain_text, value_map
    profile = getattr(settings, "profile", None) or {}
    values = value_map(profile) if profile.get("personal") else {}
    answers = [(k.replace("_", " ").capitalize(), values[k]) for k in ANSWER_SHEET_KEYS if values.get(k)]
    letter = ""
    cv = local_file(job.get("cv_path") or "")
    meta = cv.parent / "tailoring.json" if cv else None
    if meta and meta.is_file() and profile.get("personal"):
        try:
            letter = letter_plain_text(job, profile, json.loads(meta.read_text(encoding="utf-8"))["letter"])
        except (KeyError, ValueError):
            letter = ""
    return {"answers": answers, "letter": letter}


def _prepare(settings, store, job):
    from .pipeline import prepare_job
    return prepare_job(settings, store, job)


def create_app(settings: Settings | None = None, store: Store | None = None, launcher=None, opener=None,
               checker=None, browser=None, preparer=None, fetcher=None):
    settings = settings or load_settings()
    store = store or Store(settings.database_url)
    search = settings.search
    limit = int(search.get("max_job_age_days") or 0)
    default_age = next((k for k, v in AGE_CHOICES.items() if v == limit), "7")   # your setting, else 7 days
    token = secrets.token_urlsafe(24)               # forms carry it: other websites cannot post to this page
    busy: dict[str, str] = {}                       # uid -> "preparing" / "failed: ..."
    app = FastAPI(title="Job Hunter", docs_url=None, redoc_url=None, openapi_url=None)
    pages = Jinja2Templates(directory=str(Path(__file__).parent / "dashboard_templates"))
    pages.env.globals.update(age_text=age_text)
    pages.env.filters["link"] = web_link
    app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "dashboard_static")), name="static")

    def still_open(job: dict) -> bool | None:
        if checker:
            return checker(job)
        from .collectors.base import make_session
        from .linkcheck import ad_closed
        session = make_session()
        for url in dict.fromkeys(u for u in (job.get("url"), job.get("apply_url")) if u):
            if ad_closed(session, url):
                return False
        return None

    def needs_ad(job: dict) -> bool:
        return (job.get("status") in OPEN and len(job.get("description") or "") < 100
                and (job.get("url") or "").startswith("http") and "glassdoor." not in job.get("url", ""))

    def read_ad(job: dict) -> dict | None:
        """Fetch the ad text of one job and score it properly. Returns the updated job, or None."""
        try:
            if fetcher:
                text = fetcher(job["url"])
            else:
                from .collectors import description_for
                from .collectors.base import make_session
                text = description_for(make_session(), job["url"])
        except Exception as exc:
            log.debug("Could not read the ad %s: %s", job.get("url"), exc)
            return None
        if len(text or "") < 100:
            return None
        from .pipeline import rescore_open_jobs
        store.update(job["uid"], description=text)
        rescore_open_jobs(store, search)
        return store.get(job["uid"])

    ads_busy = {"running": False, "done": 0}

    @app.post("/read-ads")
    def read_missing_ads(token_: str = Form("", alias="token")):
        """Fetch the ad text for every open job that has none, in the background."""
        check(token_)
        if not ads_busy["running"]:
            ads_busy.update(running=True, done=0)
            todo = [r for s_ in OPEN for r in store.list(status=s_, limit=5000) if needs_ad(r)]

            def work():
                import time
                try:
                    for row in todo[:80]:
                        if read_ad(row):
                            ads_busy["done"] += 1
                        if not fetcher:
                            time.sleep(1.5)               # be gentle with LinkedIn
                finally:
                    ads_busy["running"] = False
            threading.Thread(target=work, daemon=True).start()
        return RedirectResponse("/?reading=1", status_code=303)

    def launch(uid: str) -> None:                   # "Apply": the form filler in its own window
        if launcher:
            return launcher(uid)
        flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
        subprocess.Popen([sys.executable, "-m", "jobhunter", "fill", uid], cwd=str(ROOT), creationflags=flags)  # nosec B603

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if request.method == "POST":
            origin = request.headers.get("origin") or request.headers.get("referer") or ""
            if origin and not origin.startswith(("http://127.0.0.1", "http://localhost")):
                return HTMLResponse("Refused: request from another website.", status_code=403)
        response = await call_next(request)
        response.headers.update({"X-Frame-Options": "DENY", "X-Content-Type-Options": "nosniff",
                                 "Content-Security-Policy": "default-src 'self'; style-src 'self' 'unsafe-inline'"})
        return response

    def check(form_token: str) -> None:
        if not secrets.compare_digest(form_token or "", token):
            raise HTTPException(status_code=400, detail="This page is out of date. Reload it and try again.")

    def view(request, name, **ctx):
        from .upload import UPLOAD_DIR
        folder = getattr(settings, "upload_dir", None) or UPLOAD_DIR
        try:                                        # whose CV is in _UPLOAD right now
            ctx["upload_for"] = (folder / "answers.txt").read_text(encoding="utf-8").splitlines()[0]
        except (OSError, IndexError):
            ctx["upload_for"] = ""
        counts = store.stats()
        ctx.update(token=token, counts=counts, version=VERSION, open_count=sum(counts.get(s, 0) for s in OPEN),
                   applied_count=sum(counts.get(s, 0) for s in APPLIED))
        return pages.TemplateResponse(request, name, ctx)

    def enrich(r: dict) -> dict:
        r = dict(r)
        r["country"] = country_of(r.get("location"), search)
        r["why"] = fit_summary(r, search) if r["status"] != "skipped" else ""
        r["busy"] = busy.get(r["uid"], "")
        return r

    @app.get("/", response_class=HTMLResponse)
    def jobs(request: Request, status: str = "open", country: str = "", min_score: int = 0, q: str = "",
             age: str = "", lang: str = ""):
        age = age if age in AGE_CHOICES else default_age
        if status == "open":
            rows = [r for s in OPEN for r in store.list(status=s, limit=5000)]
        elif status in STATUSES:
            rows = store.list(status=status, limit=5000)
        else:
            rows = store.list(limit=5000)
        rows = [enrich(r) for r in rows if (r.get("score") or 0) >= min_score
                and (not q or q.lower() in f"{r['company']} {r['title']}".lower())]
        if age != "all":                                   # posted within the last N days
            rows = [r for r in rows if (job_age(r) if job_age(r) is not None else 0) <= AGE_CHOICES[age]]
        if lang in ("en", "de"):                           # English-only ads, or ads asking for German
            from .filters import german_ask
            rows = [r for r in rows if bool(german_ask(r.get("title") or "", r.get("description") or "")[0])
                    == (lang == "de")]
        from .hidden import is_hidden, norm
        rows = [r for r in rows if not is_hidden(r.get("company") or "", search)]
        best: dict = {}                                    # the same job from several alerts: show it once
        for r in sorted(rows, key=lambda r: (-(r.get("score") or 0), -len(r.get("description") or ""))):
            best.setdefault((norm(r.get("company") or ""), norm(r.get("title") or "")), r)
        rows = list(best.values())
        countries = sorted({r["country"] for r in rows})
        if country:
            rows = [r for r in rows if r["country"] == country]
        rows.sort(key=lambda r: str(r.get("posted_at") or ""), reverse=True)     # newest first ...
        rows.sort(key=lambda r: -(r.get("score") or 0))                          # ... within each score
        no_ad = sum(1 for r in rows if needs_ad(r))
        return view(request, "jobs.html", rows=rows[:300], no_ad=no_ad, ads_busy=ads_busy, status=status, country=country, countries=countries,
                    min_score=min_score, q=q, statuses=STATUSES, age=age, age_choices=AGE_LABELS, lang=lang)

    @app.get("/tracker", response_class=HTMLResponse)
    def tracker(request: Request):
        rows = [enrich(r) for s in APPLIED for r in store.list(status=s, limit=5000)]
        rows.sort(key=lambda r: str(r.get("applied_at") or ""), reverse=True)
        for r in rows:
            r["days"] = age_days(str(r.get("applied_at") or "")) if r.get("applied_at") else None
        return view(request, "tracker.html", rows=rows, statuses=STATUSES)

    @app.get("/charts", response_class=HTMLResponse)
    def charts(request: Request):
        data = charts_data(store.list(limit=100_000, include_skipped=True), search)
        from . import discovery
        found = discovery.companies()
        data["companies"] = len(settings.sources.get("companies") or []) + len(found) \
            if hasattr(settings, "sources") else len(found)
        data["companies_hiring"] = sum(1 for c in found.values() if c.get("jobs"))
        from . import employers
        data["employers"] = len(employers.load().get("firms", {}))
        data["looked_up"] = len(discovery.load().get("_tried", {}))
        return view(request, "charts.html", d=data, week_svg=bar_chart(data["per_week"]),
                    source_svg=bar_chart(data["by_source"]), country_svg=bar_chart(data["by_country"]))

    @app.get("/job/{uid}", response_class=HTMLResponse)
    def job_page(request: Request, uid: str):
        job = store.get(uid)
        if not job:
            raise HTTPException(status_code=404, detail="No such job")
        if needs_ad(job):                               # alert emails give no ad text: read the ad now
            fetched = read_ad(job)
            if fetched:
                job = fetched
        return view(request, "job.html", j=enrich(job), statuses=STATUSES,
                    earlier=store.applied_versions(job), kit=manual_kit(job, settings))

    @app.post("/job/{uid}/files")
    def files_to_upload_folder(uid: str, token_: str = Form("", alias="token")):
        """Put this job's CV + letter in job-hunter/_UPLOAD and open that folder."""
        from .autofill import answer_sheet
        from .upload import UPLOAD_DIR, fill_upload_folder, open_folder
        check(token_)
        job = store.get(uid)
        cv = local_file((job or {}).get("cv_path") or "")
        if not job or not cv:
            raise HTTPException(status_code=400, detail="Prepare the CV and cover letter first.")
        folder = getattr(settings, "upload_dir", None) or UPLOAD_DIR
        try:
            fill_upload_folder(job, cv, local_file(job.get("letter_path") or ""),
                               answer_sheet(job, settings.profile), settings.profile, folder=folder)
        except OSError as exc:
            raise HTTPException(status_code=409, detail=f"Close the file in Word first ({exc}).") from None
        (opener or open_folder)(folder)
        return RedirectResponse(f"/job/{job['uid']}?files=1", status_code=303)

    @app.post("/job/{uid}/status")
    def set_status(uid: str, status: str = Form(...), token_: str = Form("", alias="token"), back: str = Form("/")):
        check(token_)
        job = store.get(uid)
        if not job or status not in STATUSES:
            raise HTTPException(status_code=400, detail="Unknown job or status")
        if status == "applied":
            store.mark_applied(job["uid"])
        else:
            store.update(job["uid"], status=status)
        return RedirectResponse(back if back.startswith("/") else "/", status_code=303)

    @app.post("/job/{uid}/hide-company")
    def hide_company(uid: str, token_: str = Form("", alias="token")):
        check(token_)
        job = store.get(uid)
        if not job or not job.get("company"):
            raise HTTPException(status_code=404, detail="No such job")
        from . import hidden
        hidden.add(job["company"])
        for r in store.list(limit=100_000):            # its open ads go too
            if r.get("status") in OPEN and hidden.is_hidden(r.get("company") or ""):
                store.update(r["uid"], status="skipped", notes=((r.get("notes") or "") +
                                                                "\ncompany hidden by you").strip())
        return RedirectResponse("/?hidden=" + quote(job["company"]), status_code=303)

    @app.post("/job/{uid}/prepare")
    def prepare(uid: str, token_: str = Form("", alias="token")):
        check(token_)
        job = store.get(uid)
        if not job:
            raise HTTPException(status_code=404, detail="No such job")
        if busy.get(job["uid"]) != "preparing":
            busy[job["uid"]] = "preparing"

            def work():
                from .pipeline import prepare_job
                try:
                    prepare_job(settings, store, job)
                    busy.pop(job["uid"], None)
                except Exception as exc:                        # shown on the page; the run can be repeated
                    log.warning("Preparing %s failed: %s", job["uid"], exc)
                    busy[job["uid"]] = f"failed: {exc}"[:200]
            threading.Thread(target=work, daemon=True).start()
        return RedirectResponse(f"/job/{job['uid']}", status_code=303)

    @app.post("/job/{uid}/apply")
    def apply(uid: str, token_: str = Form("", alias="token")):
        check(token_)
        job = store.get(uid)
        if not job:
            raise HTTPException(status_code=404, detail="No such job")
        if still_open(job) is False:                    # the ad says it is closed: don't open a dead form
            store.update(job["uid"], status="skipped", excluded_reason="job closed (ad says it no longer accepts applications)")
            return RedirectResponse(f"/job/{job['uid']}?closed=1", status_code=303)
        launch(job["uid"])
        return RedirectResponse(f"/job/{job['uid']}?filling=1", status_code=303)

    @app.post("/job/{uid}/manual")
    def apply_myself(uid: str, token_: str = Form("", alias="token")):
        """Apply yourself in Chrome: this job's CV + letter go to _UPLOAD, the folder and the form open."""
        from .autofill import answer_sheet, target_url
        from .upload import UPLOAD_DIR, fill_upload_folder, open_folder, open_in_browser
        check(token_)
        job = store.get(uid)
        if not job:
            raise HTTPException(status_code=404, detail="No such job")
        if still_open(job) is False:
            store.update(job["uid"], status="skipped", excluded_reason="job closed (ad says it no longer accepts applications)")
            return RedirectResponse(f"/job/{job['uid']}?closed=1", status_code=303)
        cv = local_file(job.get("cv_path") or "")
        if not cv:                                      # no CV yet: write it now (takes about a minute)
            try:
                (preparer or _prepare)(settings, store, job)
            except Exception as exc:
                raise HTTPException(status_code=500, detail=f"Could not write the CV: {exc}") from None
            job = store.get(job["uid"])
            cv = local_file(job.get("cv_path") or "")
            if not cv:
                raise HTTPException(status_code=500, detail="Could not write the CV. Try 'Prepare CV + letter'.")
        folder = getattr(settings, "upload_dir", None) or UPLOAD_DIR
        try:
            fill_upload_folder(job, cv, local_file(job.get("letter_path") or ""),
                               answer_sheet(job, settings.profile), settings.profile, folder=folder)
        except OSError as exc:
            raise HTTPException(status_code=409, detail=f"Close the file in Word first ({exc}).") from None
        (opener or open_folder)(folder)
        (browser or open_in_browser)(target_url(job))
        return RedirectResponse(f"/job/{job['uid']}?myself=1", status_code=303)

    @app.get("/file/{uid}/{kind}")
    def file(uid: str, kind: str):
        job = store.get(uid)
        path = local_file((job or {}).get({"cv": "cv_path", "letter": "letter_path"}.get(kind, ""), "") or "")
        if not path:
            raise HTTPException(status_code=404, detail="File not found")
        return FileResponse(path, filename=path.name)

    return app


def already_running(port: int) -> bool:
    import socket
    with socket.socket() as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def serve(port: int = 8095, open_browser: bool = True) -> None:
    import uvicorn
    from .upload import open_in_browser
    if already_running(port):                     # started at sign-in already: just show it
        if open_browser:
            open_in_browser(f"http://127.0.0.1:{port}")
        print("The dashboard is already running - opened it in your browser. You can close this window.")
        return
    if open_browser:
        threading.Timer(1.5, lambda: open_in_browser(f"http://127.0.0.1:{port}")).start()
    threading.Thread(target=_restart_when_updated, daemon=True).start()
    uvicorn.run(create_app(), host="127.0.0.1", port=port, log_level="warning")


def _code_stamp() -> float:
    here = Path(__file__).parent
    return max((p.stat().st_mtime for pattern in ("*.py", "collectors/*.py", "dashboard_templates/*.html")
                for p in here.glob(pattern)), default=0.0)


def _restart_when_updated(every: int = 30) -> None:
    """New Job Hunter code on disk: stop with code 3 so dashboard.bat starts the new version at once."""
    import os
    import time
    start = _code_stamp()
    while True:
        time.sleep(every)
        try:
            if _code_stamp() > start:
                time.sleep(5)                      # let the rest of the update land
                os._exit(3)
        except OSError:
            pass
