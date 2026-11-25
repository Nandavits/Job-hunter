"""The dashboard shows when the job search last ran (and warns when it stopped)."""
import datetime as dt

from jobhunter import runstatus


def test_last_run_label(tmp_path, monkeypatch):
    monkeypatch.setattr(runstatus, "FILE", tmp_path / "last-run.json")
    assert "No job search" in runstatus.label()["text"]
    runstatus.update(started="2026-11-25T18:06:00", running=True, every_minutes=30)
    now = dt.datetime(2026, 11, 25, 18, 7)
    assert runstatus.label(now)["text"].startswith("Searching for jobs now (started 18:06)")
    runstatus.update(new=4, matching=20)
    runstatus.update(running=False, finished="2026-11-25T18:10:00", ok=True)
    lab = runstatus.label(now.replace(minute=20))
    assert lab == {"text": "Last search 18:10 · 4 new jobs · next about 18:40", "warn": False}
    late = runstatus.label(dt.datetime(2026, 11, 25, 20, 0))
    assert late["warn"] and "Docker Desktop" in late["text"]
    runstatus.update(started="2026-11-25T18:40:00", running=True)
    assert "last finished 18:10" in runstatus.label(dt.datetime(2026, 11, 25, 18, 41))["text"]


def test_scheduler_and_run_both_marking_the_start_keep_the_last_finish(tmp_path, monkeypatch):
    monkeypatch.setattr(runstatus, "FILE", tmp_path / "r.json")
    runstatus.update(running=False, finished="2026-11-25T18:10:00", ok=True, new=2)
    runstatus.update(started="2026-11-25T18:40:00", running=True)     # scheduler
    runstatus.update(started="2026-11-25T18:40:01", running=True)     # the run itself
    assert runstatus.load()["last_finished"] == "2026-11-25T18:10:00"
