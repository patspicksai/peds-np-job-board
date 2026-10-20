from scraper import attribution as a

HMH = [
    {"slug": "k-hovnanian-childrens-neptune", "name": "K. Hovnanian Children's Hospital", "parent_hospital": "Jersey Shore University Medical Center",
     "health_system": "Hackensack Meridian Health", "hospital_type": "childrens_hospital_within_hospital", "city": "Neptune",
     "ats_config": {"location_aliases": ["Jersey Shore University Medical Center"]}},
    {"slug": "sanzari-childrens-hackensack", "name": "Joseph M. Sanzari Children's Hospital", "parent_hospital": "Hackensack University Medical Center",
     "health_system": "Hackensack Meridian Health", "hospital_type": "childrens_hospital_within_hospital", "city": "Hackensack",
     "ats_config": {"location_aliases": ["Hackensack University Med Cntr"]}},
]


def test_alias_match():
    h, lvl = a.attribute(HMH, "JERSEY SHORE UNIVERSITY MEDICAL CENTER, Borough of Neptune City, NJ")
    assert h["slug"] == "k-hovnanian-childrens-neptune" and lvl == "name"
    h, lvl = a.attribute(HMH, "HACKENSACK UNIVERSITY MED CNTR, Hackensack, NJ")
    assert h["slug"] == "sanzari-childrens-hackensack" and lvl == "name"


def test_city_match_and_flagship_fallback():
    h, lvl = a.attribute(HMH, "Hackensack, NJ")
    assert h["slug"] == "sanzari-childrens-hackensack" and lvl in ("name", "city")
    h, lvl = a.attribute(HMH, "Edison, NJ")
    assert lvl == "none"


def test_distinctive_tokens_skip_generic_words():
    toks = a.distinctive_tokens(HMH[0])
    assert "hovnanian" in toks and "shore" in toks and "hospital" not in toks and "children" not in toks
    assert "jersey" not in toks  # state names are not distinctive
