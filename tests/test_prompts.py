import pytest

from tracker.checks.base import (CallBudget, Reference, SerpResult, STATUS_FAILED, STATUS_INCOMPLETE,
                                 STATUS_NO_AIO, STATUS_OK)
from tracker.checks.fake import FakeProvider
from tracker.config import Blog, Cluster, GscQuery
from tracker.prompts import ACTIVE, Candidate, classify_intent, draft_candidates, select_prompts
from tracker.prompts.generate import clean_prompt, parse_searches

CLUSTERS = [Cluster("fde", "Forward Deployed Engineer", 1.0, True, ["fde"]),
            Cluster("genai", "Generative AI", 0.5, True, ["genai"])]
SETTINGS = {"gsc_match_threshold": 85, "dedupe_threshold": 90, "keep_top": 50, "probe_unmatched": True}


def cand(prompt, cluster="genai"):
    intent, w = classify_intent(prompt)
    return Candidate(prompt, cluster, "blog", intent, w)


def aio(q):
    return SerpResult(q, STATUS_OK, shown=True, references=[Reference("https://a.com", position=1)])


class FakeDrafter:
    def __init__(self, replies):
        self.replies = replies

    def draft(self, blog, cluster, n):
        return self.replies[blog.title][:n]


@pytest.mark.parametrize("prompt,intent", [
    ("genai course fees in india", "fees"),
    ("best genai course in india", "best"),
    ("genai vs data science", "comparison"),
    ("is genai course worth it", "worth"),
    ("genai for working professionals", "audience"),
    ("what is genai", "other"),
])
def test_classify_intent(prompt, intent):
    assert classify_intent(prompt)[0] == intent


def test_parse_and_clean():
    assert parse_searches('```json\n{"searches": ["A", 3, "b"]}\n```') == ["A", "b"]
    assert parse_searches("not json") == []
    assert clean_prompt('  "Is GenAI   Worth It?" ') == "is genai worth it"


def test_draft_skips_blogs_outside_active_clusters():
    blogs = [Blog("GenAI guide", "genai", cluster="genai"), Blog("Python", "python", cluster=None)]
    drafter = FakeDrafter({"GenAI guide": ["Best GenAI course?", "", "genai fees"]})
    out = draft_candidates(blogs, CLUSTERS, drafter, per_blog=4)
    assert [c.prompt for c in out] == ["best genai course", "genai fees"]


def test_gsc_match_kept_without_probe():
    provider = FakeProvider()
    gsc = [GscQuery("best genai course in india", 2000, "genai")]
    ds = select_prompts([cand("best genai course in india")], gsc, CLUSTERS, provider, SETTINGS)
    assert ds[0].status == ACTIVE and ds[0].gsc_impressions == 2000
    assert provider.calls == []


def test_unmatched_kept_only_if_probe_shows_aio():
    provider = FakeProvider({"genai course fees in india": aio("genai course fees in india"),
                             "is genai worth it for freshers": SerpResult("x", STATUS_NO_AIO),
                             "genai vs ml engineer course": SerpResult.failed("x", "timeout")})
    cands = [cand("genai course fees in india"), cand("is genai worth it for freshers"),
             cand("genai vs ml engineer course")]
    ds = {d.prompt: d for d in select_prompts(cands, [], CLUSTERS, provider, SETTINGS)}
    assert ds["genai course fees in india"].status == ACTIVE
    assert "no AI Overview" in ds["is genai worth it for freshers"].reason
    assert "probe failed" in ds["genai vs ml engineer course"].reason
    assert len(provider.calls) == 3


def test_incomplete_probe_counts_as_aio_shown():
    q = "genai course fees in india"
    provider = FakeProvider({q: SerpResult(q, STATUS_INCOMPLETE, shown=True)})
    d = select_prompts([cand(q)], [], CLUSTERS, provider, SETTINGS)[0]
    assert d.status == ACTIVE and d.probe_aio
    assert any("sources not loaded" in s for s in d.sources)


def test_near_duplicates_dropped_before_probing():
    provider = FakeProvider()
    cands = [cand("best genai course in india"), cand("best genai courses in india"),
             cand("best genai course in india")]
    ds = select_prompts(cands, [], CLUSTERS, provider, SETTINGS)
    reasons = sorted(d.reason.split(" ")[0] for d in ds)
    assert "exact" in reasons and "near-duplicate" in reasons
    assert len(provider.calls) == 1


def test_length_filter():
    ds = select_prompts([cand("genai"), cand(" ".join(["genai"] * 13))], [], CLUSTERS, None, SETTINGS)
    assert all("length" in d.reason for d in ds)


def test_budget_exhaustion_logged_not_crashing():
    provider = FakeProvider(budget=CallBudget(1))
    cands = [cand("genai course fees india"), cand("is genai worth it today")]
    ds = select_prompts(cands, [], CLUSTERS, provider, SETTINGS)
    assert any("budget" in d.reason for d in ds)


def test_scoring_and_keep_top():
    gsc = [GscQuery("best fde course in india", 5000, "fde"), GscQuery("what is genai", 50, "genai")]
    cands = [cand("what is genai", "genai"), cand("best fde course in india", "fde")]
    ds = select_prompts(cands, gsc, CLUSTERS, None, dict(SETTINGS, keep_top=1))
    assert ds[0].prompt == "best fde course in india" and ds[0].status == ACTIVE
    assert ds[1].status == "dropped" and "below top 1" in ds[1].reason
    assert ds[0].score > ds[1].score


def test_every_candidate_gets_a_reason():
    cands = [cand("genai"), cand("best genai course in india"), cand("genai course fees")]
    ds = select_prompts(cands, [], CLUSTERS, FakeProvider(), SETTINGS)
    assert len(ds) == 3 and all(d.reason for d in ds)
