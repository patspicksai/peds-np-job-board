import json
from pathlib import Path

from scraper import detect
from scraper.adapters.workday import parse_workday_url


def h(**kw):
    base = {"slug": "x", "careers_platform": "unknown", "careers_url": None, "careers_search_url": None, "ats_config": {}}
    base.update(kw)
    return base


def test_workday_host():
    assert detect.detect_platform(h(careers_url="https://aah.wd5.myworkdayjobs.com/en-US/External")) == "workday"


def test_icims_host():
    assert detect.detect_platform(h(careers_search_url="https://careers-x.icims.com/jobs/search?ss=1")) == "icims"


def test_oracle_host_and_path():
    assert detect.detect_platform(h(careers_url="https://elar.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1")) == "oracle_cloud"
    assert detect.detect_platform(h(careers_url="https://jobs.atlantichealth.org/hcmUI/CandidateExperience/en/sites/CX_1001")) == "oracle_cloud"


def test_taleo_ultipro_eightfold():
    assert detect.detect_platform(h(careers_url="https://x.taleo.net/careersection/ext/jobsearch.ftl")) == "taleo"
    assert detect.detect_platform(h(careers_url="https://recruiting.ultipro.com/ABC1000/JobBoard/0b1c2d3e-0000-1111-2222-333344445555/")) == "ultipro"
    assert detect.detect_platform(h(careers_url="https://acme.eightfold.ai/careers")) == "eightfold"


def test_declared_platform_and_generic_fallback():
    assert detect.detect_platform(h(careers_platform="phenom", careers_url="https://careers.x.org/us/en")) == "phenom"
    assert detect.detect_platform(h(careers_platform="successfactors", careers_url="https://careers.duke.edu/")) == "successfactors"
    assert detect.detect_platform(h(careers_platform="greenhouse", careers_url="https://x.org")) == "generic"
    assert detect.detect_platform(h(careers_platform="custom", careers_url="https://x.org")) == "generic"
    assert detect.detect_platform(h(careers_platform="somethingnew", careers_url="https://x.org")) == "generic"


def test_override():
    assert detect.detect_platform(h(ats_config={"adapter": "symphony"}, careers_url="https://x.org")) == "symphony"


def test_board_key_groups_system_boards():
    a = h(slug="a", careers_url="https://aah.wd5.myworkdayjobs.com/en-US/External")
    b = h(slug="b", careers_url="https://aah.wd5.myworkdayjobs.com/External", ats_config={"site": "External"})
    assert detect.board_key(a, "workday") == detect.board_key(b, "workday")
    # generic boards: the same careers page is one board (scraped once, listings attributed by location)
    g1, g2 = h(slug="g1", careers_url="https://x.org"), h(slug="g2", careers_url="https://www.x.org/")
    assert detect.board_key(g1, "generic") == detect.board_key(g2, "generic")
    g3 = h(slug="g3", careers_url="https://x.org/careers?q=np")
    assert detect.board_key(g1, "generic") != detect.board_key(g3, "generic")
    g4 = h(slug="g4", careers_url="https://www.google.com/maps/place/?q=place_id:abc")
    assert detect.board_key(g4, "generic") == "generic|g4"


def test_parse_workday_url():
    assert parse_workday_url("https://childrensnational.wd108.myworkdayjobs.com/CN_Careers") == \
        {"host": "childrensnational.wd108.myworkdayjobs.com", "tenant": "childrensnational", "site": "CN_Careers"}
    assert parse_workday_url("https://aah.wd5.myworkdayjobs.com/en-US/External/details/x") == \
        {"host": "aah.wd5.myworkdayjobs.com", "tenant": "aah", "site": "External"}
    assert parse_workday_url("https://wd1.myworkdaysite.com/en-US/recruiting/prismahealth/PrismaHealthCorporate") == \
        {"host": "wd1.myworkdaysite.com", "tenant": "prismahealth", "site": "PrismaHealthCorporate",
         "public_prefix": "/recruiting/prismahealth"}
    assert parse_workday_url("https://www.example.org/careers") == {}
    assert parse_workday_url("https://example.org/") == {}


def test_fixture_ledger_routes_every_hospital():
    p = Path(__file__).parent / "fixtures" / "hospitals_sample.json"
    hs = json.loads(p.read_text())["hospitals"]
    assert len(hs) >= 12
    for x in hs:
        assert detect.detect_platform(x) in {"workday", "icims", "jibe", "oracle_cloud", "successfactors", "phenom",
                                             "careersite", "hopkins", "generic", "symphony", "talentbrew"}


def test_sniff_recognize_and_careers_links():
    from scraper import pipeline
    notes = []
    hosp = {"slug": "x", "careers_url": "https://www.mainegeneral.org/"}
    html = '<a href="https://mainegeneral.wd5.myworkdayjobs.com/en-US/MGH_Careers">Search jobs</a>'
    a = pipeline.recognize(html, "https://www.mainegeneral.org/careers", hosp, 1e12, notes)
    assert a.platform == "workday" and a.cfg["tenant"] == "mainegeneral" and a.cfg["site"] == "MGH_Careers"
    a = pipeline.recognize('<a href="https://careers-centrastate.icims.com/jobs/search">x</a>', "https://c.org", hosp, 1e12, [])
    assert a.platform == "icims" and a.cfg["host"] == "careers-centrastate.icims.com"
    home = ('<nav><a href="/about">About</a><a href="/careers">Careers</a><a href="https://facebook.com/careers">fb</a>'
            '<a href="https://jobs.example.org/">Jobs</a><a href="/news">News</a></nav>')
    links = pipeline.careers_links(home, "https://www.example.org/")
    assert links[0] in ("https://www.example.org/careers", "https://jobs.example.org/") and len(links) == 2
