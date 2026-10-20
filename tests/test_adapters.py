"""Adapter parsers against small hand-written fixtures shaped like the live responses captured on 2026-10-20."""
import json

from scraper import textutil
from scraper.adapters.base import parse_detail_html
from scraper.adapters.careersite import _json_from_page, parse_careersite, parse_careersite_json
from scraper.adapters.generic import extract_job_links, mine_json_for_jobs
from scraper.adapters.hopkins import parse_hopkins
from scraper.adapters.icims import TOP_REDIRECT_RE, parse_icims_search
from scraper.adapters.jibe import JibeAdapter
from scraper.adapters.phenom import ddo_from_html, parse_phenom_jobs
from scraper.adapters.successfactors import parse_sf_detail, parse_sf_search
from scraper.adapters.symphony import parse_cws_results
from scraper.adapters.talentbrew import parse_results_html
from scraper.adapters.ultipro import board_path_from_url, parse_detail_json

HOSP = {"slug": "x", "careers_search_url": "https://jobs.wakemed.org/careers-home/jobs", "ats_config": {}}


def test_successfactors_search_and_detail():
    html = """
    <span class="paginationLabel" aria-label="Results 1 – 25">Results <b>1 – 25</b> of <b>70</b></span>
    <table><tbody>
    <tr class="data-row"><td class="colTitle"><span class="jobTitle hidden-phone">
      <a href="/job/Durham-NP-Pediatric-Critical-Care-NC-27710/1413761500/" class="jobTitle-link">Nurse Practitioner or Physician Assistant - Pediatric Critical Care</a></span></td>
      <td class="colLocation"><span class="jobLocation"> Durham, NC, US, 27710 </span></td>
      <td class="colFacility"><span class="jobFacility">PEDIATRICS</span></td>
      <td class="colDate"><span class="jobDate">Oct 19, 2026 </span></td></tr>
    </tbody></table>"""
    rows, total = parse_sf_search(html, "https://careers.duke.edu")
    assert total == 70 and len(rows) == 1
    r = rows[0]
    assert r.url == "https://careers.duke.edu/job/Durham-NP-Pediatric-Critical-Care-NC-27710/1413761500/"
    assert r.posted_at == "2026-10-19" and r.external_id == "1413761500" and "Durham" in r.location_text
    detail = """<meta itemprop="datePosted" content="Fri Sep 25 07:00:00 UTC 2026">
      <div class="joblayouttoken"><span class="joblayouttoken-label">Requisition Number: </span>
        <span data-careersite-propertyid="productservice">272923 </span></div>
      <span itemprop="description"><span class="jobdescription"><p>At Duke Health we care.</p><p><strong>Duties</strong></p>
        <ul><li>Round on PICU patients</li><li>PALS required</li></ul></span></span>"""
    d = parse_sf_detail(detail, r.url)
    assert d["posted_at"] == "2026-09-25" and d["external_id"] == "272923"
    assert "At Duke Health we care.\n\nDuties" in d["description"] and "• Round on PICU patients" in d["description"]


def test_workday_listing_shape():
    from scraper.adapters.workday import WorkdayAdapter
    hosp = {"slug": "cn", "careers_url": "https://childrensnational.wd108.myworkdayjobs.com/CN_Careers", "ats_config": {}}
    a = WorkdayAdapter(hosp)
    assert a._conf() == ("childrensnational.wd108.myworkdayjobs.com", "childrensnational", "CN_Careers")


def test_jibe_listing():
    a = JibeAdapter(HOSP, host="jobs.wakemed.org")
    d = {"slug": "33239", "req_id": "33239", "title": "PA or NP, Cardiology", "description": "Overview text",
         "posted_date": "2026-10-09T13:22:00+0000", "apply_url": "https://careers-wakemed.icims.com/jobs/33239/login",
         "full_location": "Raleigh, North Carolina", "meta_data": {"canonical_url": "https://jobs.wakemed.org/jobs/33239?lang=en-us"}}
    li = a._listing(d)
    assert li.url == "https://jobs.wakemed.org/jobs/33239" and li.posted_at == "2026-10-09" and li.external_id == "33239"


def test_talentbrew_results():
    frag = """<section id="search-results-list" data-total-results="684"><ul><li>
      <a href="/job/hackensack/np-pediatric-hospitalist/19511/99815700752" data-job-id="99815700752">
      <h2>NP - Pediatric Hospitalist</h2><span class="job-facility">HACKENSACK UNIVERSITY MED CNTR</span>
      <span class="job-location">Hackensack, NJ</span></a></li></ul></section>"""
    rows, total = parse_results_html(frag, "https://jobs.hackensackmeridianhealth.org")
    assert total == 684 and rows[0].title == "NP - Pediatric Hospitalist"
    assert rows[0].url.startswith("https://jobs.hackensackmeridianhealth.org/job/hackensack/")
    assert "HACKENSACK UNIVERSITY MED CNTR" in rows[0].location_text and rows[0].extra["facility"]


def test_icims_classic_and_redirect():
    html = """<div class="iCIMS_JobsTable"><div class="row"><a class="iCIMS_Anchor" href="https://careers-x.icims.com/jobs/170079/pediatric-nurse-practitioner/job?in_iframe=1">
      <h3>Pediatric Nurse Practitioner</h3></a> <span>Location</span> <span>US-NJ-Neptune</span></div></div>"""
    rows = parse_icims_search(html, "careers-x.icims.com")
    assert rows[0].url == "https://careers-x.icims.com/jobs/170079/pediatric-nurse-practitioner/job"
    assert rows[0].external_id == "170079"
    red = "<script type=\"text/javascript\">\nwindow.top.location.href = 'https:\\/\\/jobs.wakemed.org\\/jobs';\n</script>"
    assert TOP_REDIRECT_RE.search(red).group(1).replace("\\/", "/") == "https://jobs.wakemed.org/jobs"


def test_phenom_ddo():
    payload = {"eagerLoadRefineSearch": {"totalHits": 109, "data": {"jobs": [
        {"jobId": "1027745", "reqId": "1027745", "title": "Nurse Practitioner / Physician Assistant - CICU",
         "location": "Philadelphia, Pennsylvania, United States of America", "postedDate": "2026-09-01T00:00:00.000+0000"}]}}}
    html = f"<script>phApp.ddo = {json.dumps(payload)}; phApp.experimentData = {{}};</script>"
    jobs, total = parse_phenom_jobs(ddo_from_html(html))
    assert total == 109 and jobs[0]["jobId"] == "1027745"


def test_symphony_cws_rows():
    html = """<div id="live-results-counter">23</div><div role="row" class="job clearfix  jobid-23891411">
      <div class="jobTitle"><a href="/job/23891411/nurse-practitioner-inpatient-medicine/" id="job-result0">Nurse Practitioner, Inpatient Medicine</a></div>
      <div role="cell" class="flex_column ">Pediatrics-Dept of Pediatrics</div><div role="cell" class="flex_column joblist-location ">Boston, MA</div></div>"""
    rows, total = parse_cws_results(html, "https://jobs.bostonchildrens.org")
    assert total == 23 and rows[0].external_id == "23891411" and rows[0].location_text == "Boston, MA"
    assert rows[0].extra["department"] == "Pediatrics-Dept of Pediatrics"


def test_careersite_json_and_html():
    body = {"current_page": 1, "per_page": 25, "total_entries": 1269, "entries": [
        {"id": "12926085", "permalink": "neonatal-nurse-practitioner-nnp-peds-neonatology",
         "title": "Neonatal Nurse Practitioner (NNP) - Peds Neonatology",
         "location": {"locality": "Chapel Hill", "region_abbr": "NC", "name": "UNC Main Hospital"}}]}
    page = "<html><body><pre>" + json.dumps(body).replace("&", "&amp;") + "</pre></body></html>"
    rows, total = parse_careersite_json(_json_from_page(page), "https://jobs.unchealthcare.org")
    assert total == 1269
    assert rows[0].url == "https://jobs.unchealthcare.org/jobs/12926085-neonatal-nurse-practitioner-nnp-peds-neonatology"
    assert rows[0].location_text == "UNC Main Hospital, Chapel Hill, NC"
    html = """<p>Showing 1-25 of 2159 result(s)</p><div class="jobs-section__item"><div class="row">
      <div class="large-6 columns"><h5><a href="https://jobs.unchealthcare.org/jobs/1-np-picu">NP - PICU</a></h5><h6>Job ID: 250885</h6></div>
      <div class="large-2 columns"><span>Status: </span><span>Full Time</span></div>
      <div class="large-2 columns"><span class="hide">Location: </span> Chapel Hill, NC </div>
      <div class="large-2 columns"><span>Facility/Division: </span><span>UNC Medical Center</span></div></div></div>"""
    rows, total = parse_careersite(html, "https://jobs.unchealthcare.org")
    assert total == 2159 and rows[0].external_id == "250885" and "Chapel Hill, NC" in rows[0].location_text


def test_hopkins():
    html = """<div class="search-headings"><h2>Found <strong>43</strong> jobs</h2></div><div id="job-list-section"><div>
      <a href="/job/99660/neonatal-nurse-practitioner/"><h3>APP Neonatal ICU NICU</h3></a>
      <dl><span class="job-ref"><dt>Requisition #:</dt><dd>670891</dd></span>
      <span class="location"><dt>Location:</dt><dd>Johns Hopkins Hospital, Baltimore, MD 21287</dd></span>
      <span class="schedule"><dt>Schedule:</dt><dd>Rotating Shift</dd></span></dl></div></div>"""
    rows, total = parse_hopkins(html, "https://jobs.hopkinsmedicine.org")
    assert total == 43 and rows[0].external_id == "670891" and rows[0].extra["schedule"] == "Rotating Shift"


def test_ultipro_helpers():
    assert board_path_from_url("https://recruiting.ultipro.com/ABC1000ABC/JobBoard/0b1c2d3e-0000-1111-2222-333344445555/?q=") == \
        "/ABC1000ABC/JobBoard/0b1c2d3e-0000-1111-2222-333344445555"
    html = '<script>var x = new US.Opportunity.CandidateOpportunityDetail({"Description":"<p>Peds NP</p>","RequisitionNumber":"R1"});</script>'
    assert parse_detail_json(html)["RequisitionNumber"] == "R1"


def test_generic_link_extraction_and_json_mining():
    html = """<a href="/careers">Careers</a><a href="/jobs/123/pediatric-np">Pediatric Nurse Practitioner - PICU</a>
      <a href="/jobs/124/rn">Registered Nurse</a><a href="https://www.indeed.com/viewjob?jk=1">Nurse Practitioner</a>"""
    rows = extract_job_links(html, "https://careers.example.org/search")
    assert [r.url for r in rows] == ["https://careers.example.org/jobs/123/pediatric-np"]
    js = json.dumps({"results": [{"title": "NNP - NICU", "url": "/job/9"}, {"title": "Cook", "url": "/job/10"}]})
    assert [r.url for r in mine_json_for_jobs(js, "https://x.org/")] == ["https://x.org/job/9"]


def test_jsonld_detail_clean_text():
    desc = "&lt;p&gt;Join our &lt;b&gt;PICU&lt;/b&gt; team.&lt;/p&gt;&lt;ul&gt;&lt;li&gt;CPNP-AC&lt;/li&gt;&lt;li&gt;PALS&lt;/li&gt;&lt;/ul&gt;"
    html = ('<html><body><nav>Home Jobs Login</nav><script type="application/ld+json">'
            + json.dumps({"@context": "http://schema.org", "@type": "JobPosting", "title": "NP - PICU", "description": desc,
                          "datePosted": "2026-08-27", "identifier": {"@type": "PropertyValue", "value": "R123"},
                          "employmentType": "FULL_TIME",
                          "jobLocation": {"@type": "Place", "address": {"addressLocality": "Neptune", "addressRegion": "NJ"}}})
            + "</script></body></html>")
    d = parse_detail_html(html, "https://x.org/job/1")
    assert d["description"] == "Join our PICU team.\n\n• CPNP-AC\n• PALS"
    assert d["posted_at"] == "2026-08-27" and d["external_id"] == "R123" and d["employment_type"] == "Full time"
    assert d["city"] == "Neptune" and d["state"] == "NJ"


def test_parse_date_variants():
    from datetime import date
    t = date(2026, 10, 20)
    assert textutil.parse_date("Posted Today", t) == "2026-10-20"
    assert textutil.parse_date("Posted Yesterday", t) == "2026-10-19"
    assert textutil.parse_date("Posted 12 Days Ago", t) == "2026-10-08"
    assert textutil.parse_date("Posted 30+ Days Ago", t) is None
    assert textutil.parse_date("Oct 19, 2026", t) == "2026-10-19"
    assert textutil.parse_date("Fri Sep 25 07:00:00 UTC 2026", t) == "2026-09-25"
    assert textutil.parse_date("2026-10-09T13:22:00+0000", t) == "2026-10-09"
    assert textutil.parse_date(1792368000000, t) == "2026-10-19"
    assert textutil.parse_date("", t) is None


def test_html_to_text_workday_double_escaped():
    s = "111 Michigan Avenue&amp;#xa;Washington, DC&amp;#xa;<p><b>Kids Are Our Everything.</b></p><p>Second&nbsp;para</p>"
    out = textutil.html_to_text(s)
    assert "Kids Are Our Everything.\n\nSecond para" in out and "&" not in out


def test_truncation():
    out = textutil.clean_text("word " * 3000, 6000)
    assert len(out) <= 6003 and out.endswith("...")


def test_split_location():
    assert textutil.split_location("Durham, NC, US, 27710") == ("Durham", "NC")
    assert textutil.split_location("Raleigh, North Carolina") == ("Raleigh", "NC")
    assert textutil.split_location("US-MD-Baltimore") == ("Baltimore", "MD")
    assert textutil.split_location("Johns Hopkins Hospital, Baltimore, MD 21287") == ("Baltimore", "MD")
