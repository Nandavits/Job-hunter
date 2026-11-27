"""Large employers whose own careers sites Job Hunter reads on every run.

Big companies (BMW, Bosch, Deutsche Bank, ...) rarely use the start-up job systems that the company
search finds by name (Greenhouse, Personio, Lever, Ashby). Their sites run on SAP SuccessFactors,
Workday or SmartRecruiters, so they are listed here, each checked to answer before it was added.
They are searched with your own job titles (the same words the job agency search uses), and only
jobs in Germany are kept. Add more in config.yaml under sources: companies:, e.g.
  - {name: BMW Group, ats: successfactors, host: jobs.bmwgroup.com}
  - {name: Deutsche Bank, ats: workday, host: db.wd3.myworkdayjobs.com, tenant: db, site: DBWebsite}
  - {name: Bosch, ats: smartrecruiters, slug: BoschGroup}
Switch the whole list off with  sources: big_companies: false
"""

BIG = [
    # SAP SuccessFactors (public RSS feed per career site)
    {"name": "BMW Group", "ats": "successfactors", "slug": "jobs.bmwgroup.com", "host": "jobs.bmwgroup.com"},
    {"name": "ZF Group", "ats": "successfactors", "slug": "jobs.zf.com", "host": "jobs.zf.com"},
    {"name": "Schaeffler", "ats": "successfactors", "slug": "jobs.schaeffler.com", "host": "jobs.schaeffler.com"},
    # Workday (filtered to Germany with the site's own country filter)
    {"name": "Deutsche Bank", "ats": "workday", "slug": "db/DBWebsite", "host": "db.wd3.myworkdayjobs.com",
     "tenant": "db", "site": "DBWebsite"},
    {"name": "Airbus", "ats": "workday", "slug": "ag/Airbus", "host": "ag.wd3.myworkdayjobs.com",
     "tenant": "ag", "site": "Airbus"},
    # SmartRecruiters (jobs in Germany only)
    {"name": "Bosch", "ats": "smartrecruiters", "slug": "BoschGroup"},
    {"name": "Continental", "ats": "smartrecruiters", "slug": "Continental"},
    {"name": "Delivery Hero", "ats": "smartrecruiters", "slug": "DeliveryHero"},
    {"name": "SIXT", "ats": "smartrecruiters", "slug": "Sixt"},
]
