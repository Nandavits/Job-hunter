"""The website: sign up, log in, see your jobs, change statuses, prepare documents.

Every page that shows or changes data looks up rows WITH the logged-in user's id, so one user
can never read or change another user's jobs, even by guessing ids.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from redis import Redis
from sqlalchemy import func, select, text
from sqlalchemy.exc import TimeoutError as PoolTimeout

from jobhunter.dates import age_text

from . import accounts, tasks
from .db import init_db, make_engine, make_sessionmaker
from .matching import rematch_user
from .models import Job, Match, User
from .security import Keys
from .settings import ServerSettings

STATUSES = ["new", "prepared", "applied", "interview", "rejected", "offer", "skipped"]
COOKIE = "jh_session"
LOGIN_LIMIT = 10            # failed logins per IP per 15 minutes


def create_app(settings: ServerSettings | None = None, redis: Redis | None = None, sessionmaker=None) -> FastAPI:
    settings = settings or ServerSettings.from_env()
    keys = Keys(settings.secret_key)
    if sessionmaker is None:
        engine = make_engine(settings.database_url)
        init_db(engine)
        sessionmaker = make_sessionmaker(engine)
    redis = redis or Redis.from_url(settings.redis_url)
    tasks.configure(settings, sessionmaker)
    app = FastAPI(title="Job Hunter", docs_url=None, redoc_url=None)
    pages = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
    pages.env.globals["age_text"] = age_text

    @app.exception_handler(PoolTimeout)
    async def busy(request: Request, exc: PoolTimeout):
        return HTMLResponse("Job Hunter is very busy right now. Please try again in a few seconds.", status_code=503,
                            headers={"Retry-After": "5"})

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.update({
            "X-Frame-Options": "DENY", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "same-origin",
            "Content-Security-Policy": "default-src 'self'; style-src 'self' 'unsafe-inline'; form-action 'self'; "
                                       "frame-ancestors 'none'"})
        return response

    def db():
        with sessionmaker() as session:
            yield session

    def current_user(request: Request, session=Depends(db)) -> User:
        user_id = keys.user_from_session(request.cookies.get(COOKIE))
        user = session.get(User, user_id) if user_id else None
        if user is None:
            raise HTTPException(status_code=303, headers={"Location": "/login"})
        return user

    def check_csrf(user: User, token: str) -> None:
        if not keys.csrf_ok(token, user.id):
            raise HTTPException(status_code=400, detail="This form expired. Go back, reload the page and try again.")

    def page(request: Request, name: str, **context):
        user = context.get("user")
        context.setdefault("csrf", keys.csrf_token(user.id) if user else "")
        return pages.TemplateResponse(request, name, context)

    def logged_in(user: User) -> RedirectResponse:
        response = RedirectResponse("/jobs", status_code=303)
        response.set_cookie(COOKIE, keys.session_token(user.id), httponly=True, samesite="lax",
                            secure=settings.secure_cookies, max_age=14 * 86400)
        return response

    @app.get("/health")
    def health(session=Depends(db)):
        out = {"database": "ok", "queue": "ok"}
        try:
            session.execute(text("SELECT 1"))
        except Exception:
            out["database"] = "down"
        try:
            redis.ping()
        except Exception:
            out["queue"] = "down"
        return JSONResponse(out, status_code=200 if set(out.values()) == {"ok"} else 503)

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request):
        return RedirectResponse("/jobs" if keys.user_from_session(request.cookies.get(COOKIE)) else "/login", 303)

    @app.get("/signup", response_class=HTMLResponse)
    def signup_page(request: Request):
        return page(request, "signup.html")

    @app.post("/signup")
    def signup(request: Request, email: str = Form(...), password: str = Form(...), session=Depends(db)):
        try:
            user = accounts.sign_up(session, email, password)
        except accounts.AccountError as exc:
            return page(request, "signup.html", error=str(exc), email=email)
        return logged_in(user)

    @app.get("/login", response_class=HTMLResponse)
    def login_page(request: Request):
        return page(request, "login.html")

    @app.post("/login")
    def login(request: Request, email: str = Form(...), password: str = Form(...), session=Depends(db)):
        key = f"login-fail:{request.client.host if request.client else 'x'}"
        if int(redis.get(key) or 0) >= LOGIN_LIMIT:
            return page(request, "login.html", error="Too many failed logins. Wait 15 minutes.", email=email)
        try:
            user = accounts.log_in(session, email, password)
        except accounts.AccountError as exc:
            redis.incr(key)
            redis.expire(key, 900)
            return page(request, "login.html", error=str(exc), email=email)
        redis.delete(key)
        return logged_in(user)

    @app.post("/logout")
    def logout():
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(COOKIE)
        return response

    @app.get("/jobs", response_class=HTMLResponse)
    def jobs(request: Request, status: str = "open", user: User = Depends(current_user), session=Depends(db)):
        query = select(Match, Job).join(Job, Job.id == Match.job_id).where(Match.user_id == user.id)
        if status == "open":
            query = query.where(Match.status.in_(["new", "prepared"]))
        elif status in STATUSES:
            query = query.where(Match.status == status)
        rows = session.execute(query.order_by(Match.score.desc(), Job.id.desc()).limit(200)).all()
        counts = dict(session.execute(select(Match.status, func.count()).where(Match.user_id == user.id)
                                      .group_by(Match.status)).all())
        return page(request, "jobs.html", user=user, rows=rows, status=status, counts=counts, statuses=STATUSES)

    def own_match(session, user: User, match_id: int) -> tuple[Match, Job]:
        match = session.scalar(select(Match).where(Match.id == match_id, Match.user_id == user.id))
        if match is None:
            raise HTTPException(status_code=404, detail="Not found")
        return match, session.get(Job, match.job_id)

    @app.get("/jobs/{match_id}", response_class=HTMLResponse)
    def job_page(request: Request, match_id: int, user: User = Depends(current_user), session=Depends(db)):
        match, job = own_match(session, user, match_id)
        return page(request, "job.html", user=user, m=match, job=job, statuses=STATUSES)

    @app.post("/jobs/{match_id}/status")
    def set_status(match_id: int, status: str = Form(...), csrf: str = Form(""), user: User = Depends(current_user),
                   session=Depends(db)):
        check_csrf(user, csrf)
        if status not in STATUSES:
            raise HTTPException(status_code=400, detail="Unknown status")
        match, _ = own_match(session, user, match_id)
        match.status = status
        if status == "applied" and not match.applied_at:
            match.applied_at = dt.date.today().isoformat()
        session.commit()
        return RedirectResponse(f"/jobs/{match_id}", status_code=303)

    @app.post("/jobs/{match_id}/prepare")
    def prepare(match_id: int, csrf: str = Form(""), user: User = Depends(current_user), session=Depends(db)):
        check_csrf(user, csrf)
        match, _ = own_match(session, user, match_id)
        if match.prep_state != "queued":
            match.prep_state = "queued"
            session.commit()
            tasks.queue().enqueue(tasks.prepare_documents, user.id, match.id, job_timeout=600)
        return RedirectResponse(f"/jobs/{match_id}", status_code=303)

    @app.get("/files/{match_id}/{kind}")
    def download(match_id: int, kind: str, user: User = Depends(current_user), session=Depends(db)):
        match, _ = own_match(session, user, match_id)
        raw = {"cv": match.cv_path, "letter": match.letter_path}.get(kind) or ""
        path = Path(raw).resolve() if raw else None
        own_folder = (settings.storage_dir / str(user.id)).resolve()
        if not path or not path.is_file() or own_folder not in path.parents:    # only files in your own folder
            raise HTTPException(status_code=404, detail="Not found")
        return FileResponse(path, filename=path.name)

    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request, user: User = Depends(current_user)):
        return page(request, "settings.html", user=user, profile=user.profile_yaml, search=user.settings_yaml,
                    key_hint=accounts.key_hint(keys, user))

    @app.post("/settings")
    def save_settings(request: Request, profile: str = Form(...), search: str = Form(...), csrf: str = Form(""),
                      user: User = Depends(current_user), session=Depends(db)):
        check_csrf(user, csrf)
        problems = accounts.save_settings(session, user, profile, search)
        if problems:
            return page(request, "settings.html", user=user, profile=profile, search=search, problems=problems,
                        key_hint=accounts.key_hint(keys, user))
        rematch_user(session, user.id)
        return RedirectResponse("/jobs", status_code=303)

    @app.post("/settings/key")
    def save_key(api_key: str = Form(""), csrf: str = Form(""), user: User = Depends(current_user), session=Depends(db)):
        check_csrf(user, csrf)
        accounts.save_api_key(session, keys, user, api_key)
        return RedirectResponse("/settings", status_code=303)

    return app


def app_from_env() -> FastAPI:
    return create_app()
