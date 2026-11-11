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
    assert m.organic_rank == 2 and m.rank_match == "url"


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


def _with_organic(load_fixture, name, edits):
    """Real fixture with some organic results edited, to place Varsity/interviewbit in the SERP."""
    body = load_fixture(name)
    organic = body["results"]["results"]["organic"]
    for i, fields in edits.items():
        organic[i].update(fields)
    return parse_response("q", body)


def test_rank_domain_level_when_goto_link_is_opaque(load_fixture):
    # raw5: all 8 organic links are opaque goto redirects. Put interviewbit.com at position 3.
    r = _with_organic(load_fixture, "serphouse/incomplete_fde_salary_india_mobile.json",
                      {2: {"displayed_link": "https://www.interviewbit.com"}})
    assert r.organic[2].url.startswith("https://www.google.com/goto?url=")
    m = match_citations(r)
    assert m.organic_rank == 3 and m.rank_match == "domain"


def test_rank_url_level_beats_domain_level(load_fixture):
    # raw8: position 2 is a real (non-redirect) link. Make it a Varsity URL; position 1 shows interviewbit.com.
    r = _with_organic(load_fixture, "serphouse/aio_what_is_fde_mobile.json", {
        0: {"displayed_link": "https://www.interviewbit.com"},
        1: {"link": "https://www.interviewbit.com/varsity/blog/forward-deployed-engineer/?utm_source=x"},
    })
    m = match_citations(r)
    assert m.organic_rank == 2 and m.rank_match == "url"


def test_rank_not_found_in_real_fixture(load_fixture):
    m = match_citations(parse_response("q", load_fixture("serphouse/aio_what_is_fde_mobile.json")))
    assert m.organic_rank is None and m.rank_match == ""


def test_social_results_without_display_url_never_match():
    r = SerpResult(query="q", status=STATUS_OK, organic=[
        OrganicResult("https://www.google.com/goto?url=CAESopaque", position=1, display_url="")])
    assert match_citations(r).rank_match == ""


def test_page_kind():
    from tracker.match import page_kind

    assert page_kind("https://www.interviewbit.com/varsity/blog/x/") == "blog"
    assert page_kind("https://www.scaler.com/academy/fde-program/") == "program page"
    assert page_kind("https://www.interviewbit.com/courses/x") == "program page"
    assert page_kind("https://www.scaler.com/topics/x") == "other page"
    assert page_kind("https://fde.academy/") == "" and page_kind("") == ""


def test_brand_site():
    from tracker.match import brand_site

    assert brand_site("https://www.interviewbit.com/varsity/blog/x/") == "InterviewBit"
    assert brand_site("https://www.scaler.com/academy/fde-program/") == "Scaler"
    assert brand_site("https://fde.academy/") == "" and brand_site("") == ""


def test_brand_position_is_the_first_brand_reference():
    r = SerpResult("q", "ok", shown=True, references=[
        Reference("https://a.com/x", position=1), Reference("https://www.scaler.com/academy/x", position=2),
        Reference("https://www.interviewbit.com/courses/y", position=3)])
    m = match_citations(r)
    assert m.brand_cited and m.brand_position == 2 and len(m.brand_urls) == 2


def test_every_one_of_our_pages_is_recorded_once_in_order():
    blog = "https://www.interviewbit.com/varsity/blog/fde-fees/"
    r = SerpResult("q", "ok", shown=True, references=[
        Reference("https://www.scaler.com/topics/x/", position=1), Reference("https://a.com/x", position=2),
        Reference(blog, position=3), Reference("https://scaler.com/topics/x?utm_source=g", position=4),
        Reference("https://www.interviewbit.com/courses/y", position=5)])
    m = match_citations(r)
    assert m.our_pages == [("https://www.scaler.com/topics/x/", 1), (blog, 3), ("https://www.interviewbit.com/courses/y", 5)]
    assert (m.varsity_cited, m.brand_cited, m.position, m.brand_position) == (True, True, 3, 1)
