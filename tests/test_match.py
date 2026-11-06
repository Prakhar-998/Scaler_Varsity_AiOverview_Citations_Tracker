import pytest

from tracker.checks.base import OrganicResult, Reference, SerpResult, STATUS_OK
from tracker.checks.serphouse import parse_response
from tracker.match import (is_brand, is_google_redirect, is_varsity, match_citations, normalise_url,
                           unwrap_google_redirect)

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


def test_plain_google_redirects_are_decoded_before_matching():
    # Constructed examples of the readable redirect forms (not from a capture).
    q = "https://www.google.com/url?q=https%3A%2F%2Fwww.interviewbit.com%2Fvarsity%2Fblog%2Ffde-guide%2F&sa=U"
    goto = "https://www.google.com/goto?url=https://www.interviewbit.com/varsity/blog/fde-guide/?utm_source=x"
    for link in (q, goto):
        assert normalise_url(link) == BASE
        assert is_varsity(link)


def test_real_mobile_goto_link_is_opaque(load_fixture):
    # Real SERPHouse mobile organic link (raw8, position 1, McKinsey): the url= value is an
    # encrypted token, not a URL, so it can't be decoded.
    link = load_fixture("serphouse/aio_what_is_fde_mobile.json")["results"]["results"]["organic"][0]["link"]
    assert link.startswith("https://www.google.com/goto?url=CAES")
    assert is_google_redirect(link)
    assert unwrap_google_redirect(link) is None
    assert normalise_url(link) == "google.com/goto"
    assert not is_varsity(link) and not is_brand(link)


def test_non_redirects_untouched():
    url = "https://www.linkedin.com/posts/x?utm_source=share"
    assert not is_google_redirect(url) and unwrap_google_redirect(url) == url
    assert not is_google_redirect("https://www.google.com/search?q=x")


def test_varsity_vs_brand():
    assert is_varsity("https://www.interviewbit.com/varsity/blog/x/")
    assert not is_varsity("https://www.interviewbit.com/varsity/")  # hub, not a blog post
    assert not is_varsity("https://www.interviewbit.com/varsity-blog/x")
    assert is_brand("https://www.interviewbit.com/courses/x")
    assert is_brand("https://www.scaler.com/topics/x")
    assert is_brand("https://blog.scaler.com/x")
    assert not is_brand("https://www.interviewbit.com/varsity/blog/x")
    assert not is_brand("https://notscaler.com/x")


def test_match_brand_and_competitors():
    r = SerpResult(query="q", status=STATUS_OK, shown=True, references=[
        Reference("https://www.interviewbit.com/varsity/blog/x/?utm_source=x", position=1),
        Reference("https://www.example-edu.com/full-stack", position=2),
        Reference("https://www.scaler.com/courses/full-stack-developer/", position=3),
    ], organic=[OrganicResult("https://www.example-edu.com/a", position=1),
                OrganicResult("https://www.interviewbit.com/varsity/blog/x/", position=2)])
    m = match_citations(r)
    assert m.varsity_cited and m.position == 1
    assert m.brand_cited and m.brand_urls == ["https://www.scaler.com/courses/full-stack-developer/"]
    assert m.competing_domains == ["example-edu.com"]
    assert m.organic_rank == 2


def test_match_real_fixture(load_fixture):
    m = match_citations(parse_response("q", load_fixture("serphouse/aio_what_is_fde_mobile.json")))
    assert not m.varsity_cited and not m.brand_cited
    assert m.competing_domains == ["linkedin.com", "iit.edu", "posthog.com", "roadmap.sh", "nareshit.com"]


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
