"""Large employers whose own careers sites Job Hunter reads on every run.

Big companies (BMW, Bosch, Deutsche Bank, ...) rarely use the start-up job systems that the company
search finds by name (Greenhouse, Personio, Lever, Ashby). Their sites run on SAP SuccessFactors,
Workday or SmartRecruiters. Two lists:

  BIG         checked by hand: they answer, they are used on every run.
  CANDIDATES  every other large employer in Germany we know of, with the addresses their careers site
              probably has. Job Hunter tests each address itself (a few per run, in the background),
              keeps the ones that answer and uses them from then on; the rest are tried again after
              RETRY_DAYS. Results: data/big-companies.json.

Everything is searched with your own job titles (the job agency search words); only jobs in Germany are
kept. Add more in config.yaml under sources: companies:, e.g.
  - {name: BMW Group, ats: successfactors, host: jobs.bmwgroup.com}
  - {name: Deutsche Bank, ats: workday, host: db.wd3.myworkdayjobs.com, tenant: db, site: DBWebsite}
  - {name: Bosch, ats: smartrecruiters, slug: BoschGroup}
Switch all of this off with  sources: big_companies: false
"""
from __future__ import annotations

import datetime as dt
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .config import DATA_DIR

log = logging.getLogger("jobhunter")
FILE = None              # default: data/big-companies.json (tests point it elsewhere)
CHECK_PER_RUN = 120      # candidate addresses tested per run (all ~730 within about 3 hours)
RETRY_DAYS = 30


def _sf(name, host):
    return {"name": name, "ats": "successfactors", "slug": host, "host": host}


def _wd(name, host, site):
    tenant = host.split(".")[0]
    return {"name": name, "ats": "workday", "slug": f"{tenant}/{site}", "host": host, "tenant": tenant, "site": site}


def _sr(name, slug):
    return {"name": name, "ats": "smartrecruiters", "slug": slug}


BIG = [
    _sf("BMW Group", "jobs.bmwgroup.com"),
    _sf("ZF Group", "jobs.zf.com"),
    _sf("Schaeffler", "jobs.schaeffler.com"),
    _wd("Deutsche Bank", "db.wd3.myworkdayjobs.com", "DBWebsite"),
    _wd("Airbus", "ag.wd3.myworkdayjobs.com", "Airbus"),
    _sr("Bosch", "BoschGroup"),
    _sr("Continental", "Continental"),
    _sr("Delivery Hero", "DeliveryHero"),
    _sr("SIXT", "Sixt"),
    _sr("AUTO1 Group", "Auto1"),
    _sr("Hubert Burda Media", "HubertBurdaMedia"),
    _sr("Vattenfall", "Vattenfall"),
    _sr("ServiceNow", "ServiceNow"),
]

# Large employers in Germany: company name -> its web domains. Their SuccessFactors careers site, if they
# have one, is usually jobs./careers./career./karriere. on one of these domains.
SF_DOMAINS = {
    "Mercedes-Benz": ["mercedes-benz.com"], "Daimler Truck": ["daimlertruck.com"], "Volkswagen": ["volkswagen-group.com"],
    "Audi": ["audi.com"], "Porsche": ["porsche.com"], "MAN Truck & Bus": ["man.eu"], "TRATON": ["traton.com"],
    "MAHLE": ["mahle.com"], "Knorr-Bremse": ["knorr-bremse.com"], "HELLA / FORVIA": ["hella.com", "forvia.com"],
    "Brose": ["brose.com"], "Webasto": ["webasto.com"], "Eberspächer": ["eberspaecher.com"], "Benteler": ["benteler.com"],
    "Vitesco / Schaeffler": ["vitesco-technologies.com"], "Siemens": ["siemens.com"],
    "Siemens Healthineers": ["siemens-healthineers.com"], "Siemens Energy": ["siemens-energy.com"],
    "BASF": ["basf.com"], "Bayer": ["bayer.com", "bayer.de"], "Henkel": ["henkel.com"], "Beiersdorf": ["beiersdorf.com"],
    "Merck": ["merckgroup.com"], "Covestro": ["covestro.com"], "Evonik": ["evonik.com"], "LANXESS": ["lanxess.com"],
    "WACKER": ["wacker.com"], "Fresenius": ["fresenius.com", "fresenius.de"], "B. Braun": ["bbraun.com", "bbraun.de"],
    "Boehringer Ingelheim": ["boehringer-ingelheim.com"], "Sartorius": ["sartorius.com"], "Dräger": ["draeger.com"],
    "Carl Zeiss": ["zeiss.com", "zeiss.de"], "Infineon": ["infineon.com"], "TRUMPF": ["trumpf.com"], "Festo": ["festo.com"],
    "KUKA": ["kuka.com"], "Heraeus": ["heraeus.com"], "SCHOTT": ["schott.com"], "Freudenberg": ["freudenberg.com"],
    "STIHL": ["stihl.com", "stihl.de"], "Miele": ["miele.com", "miele.de"], "BSH": ["bsh-group.com"], "Vorwerk": ["vorwerk.com"],
    "Viessmann": ["viessmann.com"], "Vaillant": ["vaillant-group.com"], "thyssenkrupp": ["thyssenkrupp.com"],
    "Salzgitter": ["salzgitter-ag.com"], "Aurubis": ["aurubis.com"], "Heidelberg Materials": ["heidelbergmaterials.com"],
    "HOCHTIEF": ["hochtief.de"], "Bilfinger": ["bilfinger.com"], "KION": ["kiongroup.com"], "Jungheinrich": ["jungheinrich.com"],
    "Linde": ["linde.com"], "Rheinmetall": ["rheinmetall.com"], "HENSOLDT": ["hensoldt.net"], "MTU Aero Engines": ["mtu.de"],
    "Diehl": ["diehl.com"], "OHB": ["ohb.de"], "Lufthansa Group": ["lufthansagroup.careers", "lufthansagroup.com"],
    "Deutsche Bahn": ["deutschebahn.com", "db.jobs"], "DB Schenker": ["dbschenker.com"], "DHL Group": ["dhl.com", "dpdhl.com"],
    "Kuehne+Nagel": ["kuehne-nagel.com"], "DACHSER": ["dachser.com"], "Hapag-Lloyd": ["hapag-lloyd.com"], "TUI": ["tuigroup.com", "tui.com"],
    "Allianz": ["allianz.com", "allianz.de"], "Munich Re": ["munichre.com"], "ERGO": ["ergo.com"], "Talanx / HDI": ["talanx.com", "hdi.group"],
    "Hannover Re": ["hannover-re.com"], "Generali Deutschland": ["generali.de"], "AXA Deutschland": ["axa.de"],
    "Commerzbank": ["commerzbank.com", "commerzbank.de"], "DZ BANK": ["dzbank.de"], "KfW": ["kfw.de"],
    "Deutsche Börse": ["deutsche-boerse.com"], "ING Deutschland": ["ing.de"], "Sparkassen Finanzgruppe": ["sparkasse.de"],
    "Deutsche Telekom": ["telekom.com", "telekom.de"], "T-Systems": ["t-systems.com"], "Vodafone": ["vodafone.com", "vodafone.de"],
    "Telefónica Deutschland": ["telefonica.de"], "1&1 / IONOS": ["1und1.net", "ionos.com", "united-internet.de"],
    "SAP": ["sap.com"], "Software AG": ["softwareag.com"], "DATEV": ["datev.de"], "adesso": ["adesso.de"], "msg": ["msg.group"],
    "Bechtle": ["bechtle.com"], "Cancom": ["cancom.com"], "GFT": ["gft.com"], "Sopra Steria": ["soprasteria.de"],
    "Capgemini": ["capgemini.com"], "Atos": ["atos.net"], "NTT DATA": ["nttdata.com"], "CGI": ["cgi.com"],
    "Bertelsmann / Arvato": ["bertelsmann.com", "arvato.com"], "RTL": ["rtl.de"], "ProSiebenSat.1": ["prosiebensat1.com"],
    "Axel Springer": ["axelspringer.com"], "Otto Group": ["ottogroup.com", "otto.de"], "Zalando": ["zalando.com"],
    "MediaMarktSaturn": ["mediamarktsaturn.com"], "METRO": ["metroag.de"], "REWE Group": ["rewe-group.com"],
    "EDEKA": ["edeka.de"], "Lidl": ["lidl.de", "lidl.com"], "Kaufland": ["kaufland.de", "kaufland.com"], "Schwarz IT": ["it.schwarz"],
    "ALDI SÜD": ["aldi-sued.de"], "ALDI Nord": ["aldi-nord.de"], "dm-drogerie markt": ["dm.de"], "Rossmann": ["rossmann.de"],
    "Douglas": ["douglas.group"], "Tchibo": ["tchibo.com", "tchibo.de"], "adidas": ["adidas-group.com", "adidas.com"],
    "PUMA": ["puma.com"], "HUGO BOSS": ["hugoboss.com"], "Würth": ["wuerth.com", "wuerth.de"], "Hornbach": ["hornbach.com"],
    "RWE": ["rwe.com"], "E.ON": ["eon.com", "eon.de"], "EnBW": ["enbw.com"], "Uniper": ["uniper.energy"], "Nordex": ["nordex-online.com"],
    "Enercon": ["enercon.de"], "Vonovia": ["vonovia.de", "vonovia.com"], "CHECK24": ["check24.de"], "Scout24": ["scout24.com"],
    "Brenntag": ["brenntag.com"], "Qiagen": ["qiagen.com"], "BioNTech": ["biontech.com", "biontech.de"],
    "Roche Deutschland": ["roche.de"], "Novartis": ["novartis.com"], "Sanofi": ["sanofi.com"], "Philips": ["philips.com"],
    "Eppendorf": ["eppendorf.com"], "Ottobock": ["ottobock.com"], "Siemens Mobility": ["mobility.siemens.com"],
    "Porsche Digital / MHP": ["mhp.com"], "CARIAD": ["cariad.technology"], "Hamburg Port Authority / HHLA": ["hhla.de"],
}
SF_PREFIXES = ("jobs", "careers", "career", "karriere")

# Workday sites we know of (host, site). Tested before use, like everything here.
WORKDAY = [
    ("Novartis", "novartis.wd3.myworkdayjobs.com", "Novartis_Careers"),
    ("NVIDIA", "nvidia.wd5.myworkdayjobs.com", "NVIDIAExternalCareerSite"),
    ("Salesforce", "salesforce.wd12.myworkdayjobs.com", "External_Career_Site"),
    ("Adobe", "adobe.wd5.myworkdayjobs.com", "external_experienced"),
    ("Intel", "intel.wd1.myworkdayjobs.com", "External"),
    ("Mastercard", "mastercard.wd1.myworkdayjobs.com", "CorporateCareers"),
    ("Pfizer", "pfizer.wd1.myworkdayjobs.com", "PfizerCareers"),
    ("GSK", "gsk.wd5.myworkdayjobs.com", "GSKCareers"),
    ("AstraZeneca", "astrazeneca.wd3.myworkdayjobs.com", "Careers"),
    ("Philips", "philips.wd3.myworkdayjobs.com", "jobs-and-careers"),
    ("Thermo Fisher", "thermofisher.wd5.myworkdayjobs.com", "ThermoFisherCareers"),
    ("Medtronic", "medtronic.wd1.myworkdayjobs.com", "MedtronicCareers"),
    ("MSD", "msd.wd5.myworkdayjobs.com", "SearchJobs"),
    ("Takeda", "takeda.wd3.myworkdayjobs.com", "External"),
    ("Sanofi", "sanofi.wd3.myworkdayjobs.com", "SanofiCareers"),
    ("BlackRock", "blackrock.wd1.myworkdayjobs.com", "BlackRock_Professional"),
    ("Workday", "workday.wd5.myworkdayjobs.com", "Workday"),
    ("Bayer", "bayer.wd3.myworkdayjobs.com", "Bayer_Careers"),
    ("Siemens Healthineers", "healthineers.wd3.myworkdayjobs.com", "Healthineers"),
    ("Covestro", "covestro.wd3.myworkdayjobs.com", "cov_external"),
    ("Deutsche Börse", "deutscheboerse.wd3.myworkdayjobs.com", "Careers"),
    ("PUMA", "puma.wd3.myworkdayjobs.com", "Jobs_at_PUMA"),
    ("adidas", "adidas.wd3.myworkdayjobs.com", "adidas_careers"),
    ("Visa", "visa.wd5.myworkdayjobs.com", "Visa"),
    ("Boehringer Ingelheim", "boehringer.wd3.myworkdayjobs.com", "BI"),
    ("Siemens Energy", "siemensenergy.wd3.myworkdayjobs.com", "SiemensEnergy"),
    ("Swiss Re", "swissre.wd3.myworkdayjobs.com", "SwissReExternalCareers"),
    ("Zurich Insurance", "zurich.wd3.myworkdayjobs.com", "Zurich"),
    ("Hapag-Lloyd", "hapaglloyd.wd3.myworkdayjobs.com", "Hapag-Lloyd"),
    ("KION Group", "kiongroup.wd3.myworkdayjobs.com", "KION_Group_Careers"),
]

# SmartRecruiters names we know of (tested: only kept if the board belongs to the company and has jobs here).
SMARTRECRUITERS = [("Primark", "Primark"), ("Visa", "Visa"), ("IKEA", "IKEA"), ("Equinix", "Equinix"),
                   ("Ubisoft", "Ubisoft"), ("McDonald's", "McDonalds"), ("Wolt", "Wolt"), ("Avery Dennison", "AveryDennison")]


def candidates() -> list[dict]:
    out = []
    for name, domains in SF_DOMAINS.items():
        out += [_sf(name, f"{p}.{d}") for d in domains for p in SF_PREFIXES]
    out += [_wd(name, host, site) for name, host, site in WORKDAY]
    out += [_sr(name, slug) for name, slug in SMARTRECRUITERS]
    big = {(c["ats"], c["slug"].lower()) for c in BIG}
    return [c for c in out if (c["ats"], c["slug"].lower()) not in big]


def _key(c: dict) -> str:
    return f"{c['ats']}:{c['slug'].lower()}"


def _path() -> Path:
    return Path(FILE) if FILE else DATA_DIR / "big-companies.json"


def _load() -> dict:
    try:
        return json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(data: dict) -> None:
    try:
        _path().parent.mkdir(parents=True, exist_ok=True)
        _path().write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        log.warning("Could not save data/big-companies.json: %s", exc)


def answers(session, c: dict) -> bool:
    """Does this careers site exist and answer with jobs?"""
    try:
        if c["ats"] == "successfactors":
            from .collectors.ats import RSS_ACCEPT
            resp = session.get(f"https://{c['host']}/services/rss/job/", params={"locale": "en_US", "keywords": ""},
                               headers=RSS_ACCEPT, timeout=12, allow_redirects=False)
            return resp.status_code == 200 and b"<rss" in resp.content[:3000] and b"<channel" in resp.content
        if c["ats"] == "workday":
            resp = session.post(f"https://{c['host']}/wday/cxs/{c['tenant']}/{c['site']}/jobs",
                                json={"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""}, timeout=12)
            return resp.status_code == 200 and int((resp.json() or {}).get("total") or 0) > 0
        if c["ats"] == "smartrecruiters":
            from .discovery import _same_company
            resp = session.get(f"https://api.smartrecruiters.com/v1/companies/{c['slug']}/postings",
                               params={"country": "de", "limit": 1}, timeout=12)
            content = (resp.json() or {}).get("content") or [] if resp.status_code == 200 else []
            return bool(content) and _same_company(c["name"], (content[0].get("company") or {}).get("name", ""))
    except Exception:
        return False
    return False


def boards(session=None, check: bool = True) -> list[dict]:
    """BIG plus every candidate that answered. Tests up to CHECK_PER_RUN untested candidates first."""
    data = _load()
    today = dt.date.today()
    todo = []
    working_names = {data[k]["name"] for k in data if data[k].get("ok")}
    for c in candidates():
        state = data.get(_key(c))
        if c["name"] in working_names and not (state and state.get("ok")):
            continue                                   # this company's site is already found
        if state is None or (not state.get("ok") and
                             (today - dt.date.fromisoformat(state.get("checked", "2000-01-01"))).days >= RETRY_DAYS):
            todo.append(c)
    todo = todo[:max(0, CHECK_PER_RUN)]
    if check and session is not None and todo:
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda c: answers(session, c), todo))
        data = _load()
        for c, ok in zip(todo, results):
            data[_key(c)] = {"name": c["name"], "ok": ok, "checked": today.isoformat()}
            if ok:
                log.info("Found the careers site of %s (%s: %s)", c["name"], c["ats"], c["slug"])
        _save(data)
        log.info("Tested %d big-company careers sites, %d answer (%d known to work)", len(todo), sum(results),
                 sum(1 for v in data.values() if v.get("ok")))
    found = [c for c in candidates() if data.get(_key(c), {}).get("ok")]
    seen, out = set(), []
    for c in BIG + found:
        if _key(c) not in seen:
            seen.add(_key(c))
            out.append(c)
    return out
