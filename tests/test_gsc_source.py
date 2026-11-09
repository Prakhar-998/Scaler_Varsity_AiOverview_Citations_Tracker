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
        q("scaler fde course fees", 3000),                # own brand: dropped
        q("interviewbit varsity genai course", 2500),     # own brand: dropped
        q("fde academy course fees", 800),                # competitor: kept + tagged
        q("forward deployed engineer salary india", 5000),
        q("best python course", 4000),                    # no active cluster: not a candidate
        q("best genai course in india", 1200),
        q("best genai courses in india", 300),            # near-duplicate of the 1200 one
    ]
    out = gsc_candidates(gsc, CLUSTERS, RULES)
    prompts = [c.prompt for c in out]
    assert "what is a forward deployed engineer" not in prompts and "best python course" not in prompts
    assert prompts[0] == "forward deployed engineer salary india"  # highest impressions first
    by = {c.prompt: c for c in out}
    assert by["scaler fde course fees"].pre_drop == "branded query ('scaler')"
    assert by["interviewbit varsity genai course"].pre_drop.startswith("branded query")
    assert by["fde academy course fees"].competitor and not by["fde academy course fees"].pre_drop
    assert "near-duplicate of higher-impression GSC query 'best genai course in india'" == \
        by["best genai courses in india"].pre_drop
    assert all(c.source == "gsc" and c.impressions > 0 for c in out)


def test_gsc_candidates_flow_through_select_without_probes():
    gsc = [q("scaler fde course fees", 3000), q("fde academy course fees", 800), q("fde salary", 600)]
    decisions = select_prompts(gsc_candidates(gsc, CLUSTERS, RULES), gsc, CLUSTERS, None, SETTINGS)
    by = {d.prompt: d for d in decisions}
    assert by["fde academy course fees"].status == ACTIVE and by["fde academy course fees"].candidate.competitor
    assert by["fde salary"].status == ACTIVE  # two-word GSC query allowed
    assert by["scaler fde course fees"].status == "dropped" and "branded" in by["scaler fde course fees"].reason


def test_brand_match_is_whole_word():
    assert RULES.brand("descaler for kettles") == ""
    assert RULES.competitor("iit madras genai course") == "iit"
    assert RULES.competitor("digital marketing course") == ""


def test_programs_yaml_has_brand_rules():
    rules = load_brand_rules()
    assert "scaler" in rules.brand_terms and "interviewbit" in rules.brand_terms
    assert "fde academy" in rules.competitors
