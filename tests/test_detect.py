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
    g1, g2 = h(slug="g1", careers_url="https://x.org"), h(slug="g2", careers_url="https://x.org")
    assert detect.board_key(g1, "generic") != detect.board_key(g2, "generic")


def test_parse_workday_url():
    assert parse_workday_url("https://childrensnational.wd108.myworkdayjobs.com/CN_Careers") == \
        {"host": "childrensnational.wd108.myworkdayjobs.com", "tenant": "childrensnational", "site": "CN_Careers"}
    assert parse_workday_url("https://aah.wd5.myworkdayjobs.com/en-US/External/details/x") == \
        {"host": "aah.wd5.myworkdayjobs.com", "tenant": "aah", "site": "External"}
    assert parse_workday_url("https://wd1.myworkdaysite.com/en-US/recruiting/prismahealth/PrismaHealthCorporate") == {}
    assert parse_workday_url("https://example.org/") == {}


def test_fixture_ledger_routes_every_hospital():
    p = Path(__file__).parent / "fixtures" / "hospitals_sample.json"
    hs = json.loads(p.read_text())["hospitals"]
    assert len(hs) >= 12
    for x in hs:
        assert detect.detect_platform(x) in {"workday", "icims", "jibe", "oracle_cloud", "successfactors", "phenom",
                                             "careersite", "hopkins", "generic", "symphony", "talentbrew"}
