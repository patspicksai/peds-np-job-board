import hashlib

from scraper.models import Job, job_id, normalize_url


def test_normalize_strips_tracking_and_trailing_slash():
    a = normalize_url("https://Jobs.Example.org/job/123/Nurse-Practitioner/?utm_source=x&src=LinkedIn&ref=abc&lang=en")
    assert a == "https://jobs.example.org/job/123/nurse-practitioner?lang=en"


def test_normalize_drops_fragment_and_default_port():
    assert normalize_url("https://jobs.example.org:443/a/b/#apply") == "https://jobs.example.org/a/b"


def test_normalize_keeps_meaningful_query():
    u = "https://x.taleo.net/careersection/ext/jobdetail.ftl?job=12345&source=indeed"
    assert normalize_url(u) == "https://x.taleo.net/careersection/ext/jobdetail.ftl?job=12345"


def test_job_id_is_sha1_prefix_of_normalized():
    u = "https://careers.duke.edu/job/Durham-NP-Peds/1413761500/"
    expect = hashlib.sha1(normalize_url(u).encode()).hexdigest()[:12]
    assert job_id(u) == expect
    assert job_id(u) == job_id("https://careers.duke.edu/job/Durham-NP-Peds/1413761500?utm_medium=email")
    assert len(job_id(u)) == 12


def test_job_to_dict_has_all_schema_fields():
    j = Job(hospital_slug="duke-childrens-durham", title="NP", url="https://careers.duke.edu/job/x/1/")
    d = j.to_dict()
    for k in ("id", "hospital_slug", "title", "url", "apply_url", "source", "external_id", "location_text", "city",
              "state", "department", "setting", "employment_type", "shift", "schedule", "salary_text", "posted_at",
              "summary", "description", "requirements", "relevance", "relevance_reason", "tags", "first_seen_at",
              "last_seen_at", "status", "raw_notes"):
        assert k in d
    assert d["id"] == job_id("https://careers.duke.edu/job/x/1/")
    assert d["source"] == "employer" and d["status"] == "active"
