from scraper import relevance as r

CHILDRENS = {"hospital_type": "childrens_hospital", "hotspot": False}
AMC = {"hospital_type": "academic_medical_center", "hotspot": False}
AMC_HOT = {"hospital_type": "academic_medical_center", "hotspot": True}


def test_picu_np_is_3():
    keep, rel, setting, reason, tags = r.is_candidate("Nurse Practitioner or Physician Assistant - Pediatric Critical Care", "", AMC)
    assert keep and rel == 3 and setting == "picu"


def test_nicu_nnp_is_3():
    v = r.classify("Neonatal Nurse Practitioner (NNP) - Peds Neonatology", "", AMC)
    assert v.keep and v.relevance == 3 and v.setting == "nicu"


def test_peds_hospitalist_is_3():
    v = r.classify("Nurse Practitioner, Inpatient Pediatric Hospital Medicine", "", AMC)
    assert v.relevance == 3 and v.setting == "peds_inpatient"


def test_peds_ed_is_3():
    v = r.classify("Pediatric Emergency Department Nurse Practitioner", "", AMC)
    assert v.relevance == 3 and v.setting == "peds_ed"


def test_peds_primary_care_is_2():
    v = r.classify("Pediatric Nurse Practitioner (NP) - UNC Health Caldwell", "Outpatient general pediatrics practice, well child visits.", AMC)
    assert v.keep and v.relevance == 2 and v.setting == "peds_primary_care"


def test_peds_outpatient_specialty_is_2():
    v = r.classify("Nurse Practitioner - Peds Endocrinology", "Outpatient clinic based role.", AMC)
    assert v.relevance == 2 and v.setting == "peds_specialty_outpatient"


def test_acute_np_at_childrens_hospital_is_3_or_2():
    v = r.classify("Nurse Practitioner, Float Pool", "Inpatient units across the hospital.", CHILDRENS)
    assert v.keep and v.relevance in (2, 3)


def test_np_at_childrens_hospital_unclear_setting_is_2():
    v = r.classify("Nurse Practitioner - Complex Care Services, per diem", "", CHILDRENS)
    assert v.keep and v.relevance >= 2


def test_adult_np_not_hotspot_dropped():
    v = r.classify("Nurse Practitioner - Adult Cardiology", "Adult patients.", AMC)
    assert not v.keep and v.relevance == 0


def test_adult_np_in_hotspot_is_1():
    v = r.classify("Hospitalist Nurse Practitioner", "Adult inpatient medicine.", AMC_HOT)
    assert v.keep and v.relevance == 1


def test_pa_only_excluded():
    v = r.classify("Physician Assistant - Pediatric Surgery", "Must be NCCPA certified physician assistant.", AMC)
    assert not v.keep


def test_pa_title_but_np_or_pa_kept():
    v = r.classify("Physician Assistant - Pediatric Surgery", "We welcome an NP or PA for this role.", AMC)
    assert v.keep and v.relevance >= 2


def test_rn_role_excluded():
    v = r.classify("Registered Nurse - NICU - Per Diem Nights", "pediatric NICU nurse", AMC)
    assert not v.keep


def test_crna_and_midwife_excluded():
    assert not r.classify("CRNA - Pediatric Anesthesia", "", AMC).keep
    assert not r.classify("Certified Nurse Midwife", "", AMC_HOT).keep


def test_app_acronym_title():
    v = r.classify("APP Critical Care PRN - NICU - Sarasota", "", AMC)
    assert v.keep and v.relevance == 3


def test_pediatric_only_in_description():
    v = r.classify("Nurse Practitioner - Hospitalist", "Care for pediatric patients in our pediatric inpatient unit.", AMC)
    assert v.keep and v.relevance >= 2


def test_tags():
    tags = r.find_tags("NP - PICU nights", "$20,000 sign on bonus. CPNP-AC required. Relocation assistance available.")
    assert "sign-on bonus" in tags and "nights" in tags and "CPNP-AC required" in tags and "relocation assistance" in tags


def test_listing_priority():
    assert r.listing_priority("Registered Nurse - PICU") == 0
    assert r.listing_priority("Nurse Practitioner - PICU") == 3
    assert r.listing_priority("Nurse Practitioner - Cardiology", "", AMC) == 1
    assert r.listing_priority("Nurse Practitioner - Cardiology", "", CHILDRENS) == 3


def test_requirements_extracted():
    reqs = r.extract_requirements("Qualifications\n• Current CPNP-AC certification\n• PALS required\n• Team player")
    assert any("CPNP-AC" in x for x in reqs) and any("PALS" in x for x in reqs)
    assert not any("Team player" in x for x in reqs)
