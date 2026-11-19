from jobhunter.linkcheck import is_closed


class Resp:
    def __init__(self, status, data=None, text=""):
        self.status_code, self._data, self.text = status, data, text

    def json(self):
        return self._data


class Session:
    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def get(self, url, timeout=0):
        self.calls.append(url)
        for key, resp in self.answers.items():
            if key in url:
                return resp
        return Resp(500)


def test_greenhouse_ashby_lever_personio():
    s = Session({
        "boards/constellr/jobs": Resp(200, {"jobs": [{"id": 111}]}),
        "job-board/statista": Resp(200, {"jobs": [{"id": "aaaaaaaa-0000-0000-0000-000000000001"}]}),
        "postings/lovehoney": Resp(200, [{"id": "bbbbbbbb-0000-0000-0000-000000000002"}]),
        "westwing.jobs.personio.de/xml": Resp(200, text="<id>2865868</id>"),
    })
    c = {}
    assert is_closed(s, "https://job-boards.eu.greenhouse.io/embed/job_app?for=constellr&token=111", c) is False
    assert is_closed(s, "https://job-boards.eu.greenhouse.io/embed/job_app?for=constellr&token=999", c) is True
    assert is_closed(s, "https://jobs.ashbyhq.com/statista/27f98734-2ba5-439a-a5c0-df5060013761", c) is True
    assert is_closed(s, "https://jobs.eu.lever.co/lovehoney/bbbbbbbb-0000-0000-0000-000000000002", c) is False
    assert is_closed(s, "https://westwing.jobs.personio.de/job/2865868?language=en#apply", c) is False
    assert sum("constellr" in u for u in s.calls) == 1                 # one board call per company


def test_unreachable_board_is_not_closed():
    assert is_closed(Session({}), "https://jobs.ashbyhq.com/x/27f98734-2ba5-439a-a5c0-df5060013761", {}) is None
    assert is_closed(Session({}), "https://lynxbroker-jobs.softgarden.io/job/64286048", {}) is None
