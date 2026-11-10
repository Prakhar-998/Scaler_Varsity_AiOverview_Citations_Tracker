import pytest

from tracker.config import BrandRules, Cluster, GscQuery, load_brand_rules
from tracker.prompts import ACTIVE, bofu_term, gsc_candidates, select_prompts

CLUSTERS = [Cluster("fde", "Forward Deployed Engineer", 1.0, True, ["forward deployed", "fde"]),
            Cluster("genai", "Generative AI", 1.0, True, ["genai", "generative ai"])]
RULES = BrandRules(["scaler", "interviewbit", "interview bit", "varsity"], ["fde academy", "upgrad", "iit"])
SETTINGS = {"gsc_match_threshold": 85, "dedupe_threshold": 90, "keep_top": 50}


def q(query, imp, cluster=None):
    for c in CLUSTERS:
        if cluster is None and c.matches(query):
            cluster = c.key
    return GscQuery(query, imp, cluster)


@pytest.mark.parametrize("query,term", [
    ("best fde course in india", "best"), ("fde salary", "salary"), ("genai classes near me", "near me"),
    ("langchain vs langgraph", "vs"), ("what is a forward deployed engineer", ""),
    ("topology basics", ""),  # "top" must be a whole word
])
def test_bofu_term(query, term):
    assert bofu_term(query) == term


def test_gsc_filter_brand_dropped_competitor_tagged_ranked_by_impressions():
    gsc = [
        q("what is a forward deployed engineer", 9000),   # no BOFU term: not a candidate
        q("scaler fde course fees", 3000),                # own brand only: dropped
        q("interviewbit varsity genai course", 2500),     # own brand only: dropped
        q("fde academy course fees", 800),                # competitor: kept + tagged
        q("forward deployed engineer salary india", 5000),
        q("best python course", 4000),                    # no active cluster: not a candidate
        q("best genai course in india", 1200),
        q("best genai courses in india", 300),            # same intent group: second one kept
        q("top generative ai course in india", 250),      # same group, already 2 kept: dropped
        q("best genai course india", 200),                # fuzzy near-duplicate of the group
    ]
    out = gsc_candidates(gsc, CLUSTERS, RULES)
    prompts = [c.prompt for c in out]
    assert "what is a forward deployed engineer" not in prompts and "best python course" not in prompts
    assert prompts[0] == "forward deployed engineer salary india"  # highest impressions first
    by = {c.prompt: c for c in out}
    assert by["scaler fde course fees"].pre_drop == "navigational / pure brand query ('scaler')"
    assert by["interviewbit varsity genai course"].pre_drop.startswith("navigational / pure brand")
    assert by["fde academy course fees"].brand_tag == "competitor" and not by["fde academy course fees"].pre_drop
    assert not by["best genai courses in india"].pre_drop
    assert by["top generative ai course in india"].pre_drop == (
        "intent group full (2 kept: 'best genai course in india' and 'best genai courses in india')")
    assert "near-duplicate of higher-impression GSC query 'best genai course in india'" == \
        by["best genai course india"].pre_drop
    assert all(c.source == "gsc" and c.impressions > 0 for c in out)


def test_gsc_candidates_flow_through_select_without_probes():
    gsc = [q("scaler fde course fees", 3000), q("fde academy course fees", 800), q("fde salary", 600)]
    decisions = select_prompts(gsc_candidates(gsc, CLUSTERS, RULES), gsc, CLUSTERS, None, SETTINGS)
    by = {d.prompt: d for d in decisions}
    assert by["fde academy course fees"].status == ACTIVE
    assert by["fde academy course fees"].candidate.brand_tag == "competitor"
    assert by["fde salary"].status == ACTIVE  # two-word GSC query allowed
    assert by["scaler fde course fees"].status == "dropped" and "pure brand" in by["scaler fde course fees"].reason


def test_brand_match_is_whole_word():
    assert RULES.brand("descaler for kettles") == ""
    assert RULES.competitor("iit madras genai course") == "iit"
    assert RULES.competitor("digital marketing course") == ""


def test_programs_yaml_has_brand_rules():
    rules = load_brand_rules()
    assert "scaler" in rules.brand_terms and "interviewbit" in rules.brand_terms
    assert "fde academy" in rules.competitors
    assert rules.competitor("fde.academy fees") == "fde.academy"  # spelling seen in real GSC data


@pytest.mark.parametrize("query,tag,dropped", [
    # own brand vs a competitor or a comparison: kept
    ("scaler vs fde academy for forward deployed engineering", "brand_comparison", False),
    ("scaler versus upgrad genai course", "brand_comparison", False),
    ("is scaler better than iit genai course", "brand_comparison", False),
    ("scaler fde course alternatives", "brand_comparison", False),
    ("compare scaler and fde academy fees", "brand_comparison", False),
    ("fde academy or scaler which is best", "brand_comparison", False),  # competitor named
    # navigational / pure brand: dropped
    ("scaler login", "", True),
    ("scaler contact number", "", True),
    ("scaler fde course reviews", "", True),
    ("interviewbit varsity", "", True),
    ("which scaler program best prepares you for a forward deployed engineering role", "", True),
    # competitor only: kept
    ("fde academy reviews", "competitor", False),
    # no brand at all
    ("best fde course in india", "", False),
])
def test_tag_brands(query, tag, dropped):
    from tracker.prompts import Candidate, tag_brands

    c = Candidate(query, "fde", "", source="gsc")
    tag_brands([c], RULES)
    assert c.brand_tag == tag
    assert bool(c.pre_drop) is dropped


def test_brand_comparison_survives_selection():
    gsc = [q("scaler vs fde academy for forward deployed engineering", 50), q("scaler fde course login", 900)]
    by = {d.prompt: d for d in select_prompts(gsc_candidates(gsc, CLUSTERS, RULES), gsc, CLUSTERS, None, SETTINGS)}
    assert by["scaler vs fde academy for forward deployed engineering"].status == ACTIVE
    assert by["scaler fde course login"].status == "dropped"


def test_fees_synonyms_and_punctuation_share_an_intent_group():
    gsc = [q("fde academy fees", 400), q("fde academy cost", 300), q("fde.academy price", 200),
           q("fees fde academy", 100), q("fde course fees", 90)]
    by = {c.prompt: c for c in gsc_candidates(gsc, CLUSTERS, RULES)}
    assert [p for p, c in by.items() if not c.pre_drop] == ["fde academy fees", "fde academy cost", "fde course fees"]
    assert by["fde.academy price"].pre_drop.startswith("intent group full")


def test_intent_key_folds_the_listed_synonyms():
    from tracker.prompts import intent_key

    assert intent_key("Top Gen AI Courses") == intent_key("best generative ai course") == "best course genai"
    assert intent_key("fde course fee") == intent_key("fde course price") == intent_key("cost fde course") != \
        intent_key("fde course")
