import base64
import json
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
FIX = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(ROOT))


class FakeResponse:
    def __init__(self, status=200, text="", data=None):
        self.status_code = status
        self.text = text if data is None else json.dumps(data)
        self._data = data
        self.content = self.text.encode("utf-8")

    def json(self):
        return self._data if self._data is not None else json.loads(self.text)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    """Routes the public job-board API URLs to local fixture files."""

    def __init__(self):
        self.calls = []
        self.headers = {}

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append((url, params))
        if "greenhouse.io/v1/boards/acme/jobs" in url:
            return FakeResponse(data=json.loads((FIX / "greenhouse.json").read_text()))
        if "lever.co/v0/postings/beta" in url:
            return FakeResponse(data=json.loads((FIX / "lever.json").read_text()))
        if "ashbyhq.com/posting-api/job-board/gamma" in url:
            return FakeResponse(data=json.loads((FIX / "ashby.json").read_text()))
        if "zeta.jobs.personio.de/xml" in url:
            return FakeResponse(text=(FIX / "personio.xml").read_text())
        if "arbeitnow.com/api/job-board-api" in url:
            if "page=2" in url:
                return FakeResponse(data={"data": [], "links": {"next": None}})
            return FakeResponse(data=json.loads((FIX / "arbeitnow.json").read_text()))
        if url.endswith("/pc/v6/jobs"):
            return FakeResponse(data=json.loads((FIX / "ba_search.json").read_text()))
        if "/pc/v4/jobdetails/" in url:
            refnr = base64.b64decode(url.rsplit("/", 1)[1]).decode()
            n = "1" if "1111" in refnr else "2"
            return FakeResponse(data=json.loads((FIX / f"ba_detail_{n}.json").read_text()))
        return FakeResponse(status=404)


@pytest.fixture
def session():
    return FakeSession()


@pytest.fixture
def config():
    cfg = yaml.safe_load((ROOT / "config" / "config.example.yaml").read_text())
    cfg["sources"]["companies"] = [
        {"name": "Acme", "ats": "greenhouse", "slug": "acme"},
        {"name": "Beta", "ats": "lever", "slug": "beta"},
        {"name": "Gamma", "ats": "ashby", "slug": "gamma"},
        {"name": "Zeta", "ats": "personio", "slug": "zeta"},
    ]
    cfg["sources"]["arbeitsagentur"]["queries"] = ["Data Engineer"]
    cfg["sources"]["arbeitsagentur"]["include_remote"] = False
    cfg["tailoring"]["make_pdf"] = False
    return cfg


@pytest.fixture
def profile():
    return yaml.safe_load((ROOT / "config" / "profile.example.yaml").read_text())


@pytest.fixture
def settings(config, profile, tmp_path, monkeypatch):
    from jobhunter import config as cfgmod
    from jobhunter import pipeline
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'jobs.db'}")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(pipeline, "OUTPUT_DIR", tmp_path / "output")
    monkeypatch.setattr(pipeline, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir()
    return cfgmod.Settings(config=config, profile=profile)
