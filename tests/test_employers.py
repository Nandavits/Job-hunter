"""Every company hiring IT/data people (job agency, once a day) -> careers pages looked up."""
from jobhunter import employers
from jobhunter.models import Job


def fake_search(pages):
    calls = []

    def search(session, params):
        calls.append((params["was"], params["page"]))
        return pages.get((params["was"], params["page"]), [])
    return search, calls


def ad(company, i):
    return Job(source="arbeitsagentur", external_id=f"{company}{i}", company=company, title="x", url="u")


def test_harvest_collects_employers_once_a_day():
    full = [ad("smartclip Europe GmbH", i) for i in range(60)] + [ad("Tiny Startup UG", i) for i in range(40)]
    search, calls = fake_search({("Data", 1): full, ("Data", 2): [ad("Other AG", 1)], ("Python", 1): [ad("Tiny Startup UG", 99)]})
    assert employers.harvest(None, queries=["Data", "Python"], search=search) == 3
    assert ("Data", 3) not in calls                     # page 2 was not full: no page 3
    names = employers.names()
    assert names[0] == "smartclip Europe GmbH" and set(names) == {"smartclip Europe GmbH", "Tiny Startup UG", "Other AG"}
    before = len(calls)
    assert employers.harvest(None, queries=["Data"], search=search) == 0    # already done today
    assert len(calls) == before


def test_agencies_are_not_looked_up(tmp_path):
    from jobhunter import discovery
    data = {}
    picked = discovery._pick(["Hays Professional Solutions GmbH", "Brunel GmbH NL Nürnberg", "Bechtle AG",
                              "Rockstar Recruiting AG", "FERCHAU GmbH Niederlassung Köln"], data, [], 10)
    assert picked == ["Bechtle AG"]
