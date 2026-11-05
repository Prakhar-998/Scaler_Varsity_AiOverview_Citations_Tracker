import pytest

from tracker.checks.base import OrganicResult, Reference, SerpResult, STATUS_OK
from tracker.checks.serphouse import parse_response
from tracker.match import is_brand, is_varsity, match_citations, normalise_url

BASE = "interviewbit.com/varsity/blog/fde-guide"


@pytest.mark.parametrize("url", [
    "https://www.interviewbit.com/varsity/blog/fde-guide/",
    "http://interviewbit.com/varsity/blog/fde-guide",
    "https://www.interviewbit.com/varsity/blog/fde-guide/?utm_source=google&utm_medium=x",
    "https://www.interviewbit.com/varsity/blog/fde-guide/#:~:text=fees",
    "https://www.interviewbit.com/varsity/blog/fde-guide/amp/",
    "https://amp.interviewbit.com/varsity/blog/fde-guide",
    "https://www.google.com/amp/s/www.interviewbit.com/varsity/blog/fde-guide/amp/",
    "HTTPS://WWW.InterviewBit.com/Varsity/Blog/FDE-Guide",
])
def test_normalise_variants_collapse(url):
    assert normalise_url(url) == BASE


def test_varsity_vs_brand():
    assert is_varsity("https://www.interviewbit.com/varsity/blog/x/")
    assert not is_varsity("https://www.interviewbit.com/varsity/")  # hub, not a blog post
    assert not is_varsity("https://www.interviewbit.com/varsity-blog/x")
    assert is_brand("https://www.interviewbit.com/courses/x")
    assert is_brand("https://www.scaler.com/topics/x")
    assert is_brand("https://blog.scaler.com/x")
    assert not is_brand("https://www.interviewbit.com/varsity/blog/x")
    assert not is_brand("https://notscaler.com/x")


def test_match_from_fixture(load_fixture):
    m = match_citations(parse_response("q", load_fixture("synthetic_serphouse_aio.json")))
    assert m.varsity_cited and m.position == 1
    assert m.brand_cited and m.brand_urls == ["https://www.scaler.com/courses/full-stack-developer/"]
    assert m.competing_domains == ["example-edu.com"]
    assert m.organic_rank == 2


def test_no_varsity():
    r = SerpResult(query="q", status=STATUS_OK, shown=True,
                   references=[Reference("https://a.com/1", position=1), Reference("https://b.com", position=2)],
                   organic=[OrganicResult("https://a.com/1", position=1)])
    m = match_citations(r)
    assert not m.varsity_cited and m.position is None and m.organic_rank is None
    assert m.competing_domains == ["a.com", "b.com"]


def test_varsity_position_is_first_occurrence():
    r = SerpResult(query="q", status=STATUS_OK, shown=True, references=[
        Reference("https://a.com", position=1),
        Reference("https://www.interviewbit.com/varsity/blog/one/", position=2),
        Reference("https://www.interviewbit.com/varsity/blog/two/", position=3),
    ])
    m = match_citations(r)
    assert m.position == 2 and m.cited_url.endswith("/one/")
