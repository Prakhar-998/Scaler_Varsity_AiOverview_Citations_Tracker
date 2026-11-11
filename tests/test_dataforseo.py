import copy

import requests
import pytest

from tracker.checks import CallBudget, get_provider
from tracker.config import Blog
from tracker.checks.base import STATUS_FAILED, STATUS_INCOMPLETE, STATUS_NO_AIO, STATUS_OK
from tracker.checks.dataforseo import ENDPOINT, DataForSeoProvider, parse_response
from tracker.match import domain_of, match_citations


class FakeResponse:
    def __init__(self, status_code=200, body=None, text=""):
        self.status_code = status_code
        self._body = body
        self.text = text

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, json=None, headers=None, auth=None, timeout=None):
        self.calls.append({"url": url, "json": json, "auth": auth})
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def make(responses, **kw):
    session = FakeSession(responses)
    return DataForSeoProvider("login", "pw", session=session, backoff=0, incomplete_delay=0,
                              transient_delay=0, **kw), session


MOBILE = "dataforseo/aio_fde_salary_india_mobile.json"
DESKTOP = "dataforseo/aio_fde_salary_india_desktop.json"
NO_TASKS = "dataforseo/no_tasks_ok_envelope.json"
LEARNING_PATHS = "dataforseo/aio_fde_learning_paths_desktop.json"  # 2026-11-11 13:26 run
AIO = DESKTOP


def items_of(body):
    return body["tasks"][0]["result"][0]["items"]


def incomplete(load_fixture):
    body = copy.deepcopy(load_fixture(AIO))
    aio = next(i for i in items_of(body) if i["type"] == "ai_overview")
    aio["references"] = []
    for element in aio["items"]:
        element["references"] = []
    return body


def test_request_body_and_auth(load_fixture):
    provider, session = make([FakeResponse(body=load_fixture(AIO))])
    provider.fetch_ai_overview("fde salary")
    call = session.calls[0]
    assert call["url"] == ENDPOINT and call["auth"] == ("login", "pw")
    assert call["json"] == [{"keyword": "fde salary", "location_code": 2356, "language_code": "en",
                             "device": "mobile", "load_async_ai_overview": True}]


def test_device_is_sent_per_provider(load_fixture):
    session = FakeSession([FakeResponse(body=load_fixture(AIO))])
    DataForSeoProvider("l", "p", session=session, device="desktop").fetch_ai_overview("q")
    assert session.calls[0]["json"][0]["device"] == "desktop"


def test_real_desktop_sources_list_then_inline_citations(load_fixture):
    result = parse_response("forward deployed engineer salary in india", load_fixture(DESKTOP))
    assert result.status == STATUS_OK and result.shown
    # 3 in the sources list, then 3 more cited only inline in the overview's sections
    assert [domain_of(r.url) for r in result.references] == [
        "linkedin.com", "fde.academy", "ambitionbox.com", "buildfastwithai.com", "secondtalent.com", "nareshit.com"]
    assert [r.position for r in result.references] == [1, 2, 3, 4, 5, 6]
    assert result.references[1].source == "FDE Academy"
    m = match_citations(result)
    assert not m.varsity_cited and not m.brand_cited
    assert m.competing_domains[:3] == ["linkedin.com", "fde.academy", "ambitionbox.com"]


def test_real_mobile_references_keep_source_and_title_for_goto_links(load_fixture):
    result = parse_response("forward deployed engineer salary in india", load_fixture(MOBILE))
    assert result.status == STATUS_OK and len(result.references) == 6
    goto = [r for r in result.references if domain_of(r.url) == "google.com"]
    assert len(goto) == 4
    assert [r.source for r in goto] == ["Reddit", "Arivu Skills", "AmbitionBox", "www.glassdoor.co.in"]
    assert all(r.title for r in goto)
    # opaque links aren't reported as a "google.com" competitor
    assert match_citations(result).competing_domains == ["arivuskills.com", "glassdoor.co.in"]


def test_real_organic_urls_are_redirects_so_site_comes_from_breadcrumb(load_fixture):
    organic = parse_response("q", load_fixture(DESKTOP)).organic
    assert len(organic) == 9 and all(o.url.startswith("https://google.com/goto?url=") for o in organic)
    assert organic[0].display_url == ""  # LinkedIn: "740+ reactions · 3 months ago"
    assert organic[1].display_url == "https://www.ambitionbox.com"
    assert [o.position for o in organic] == list(range(1, 10))  # rank_group, ignoring the AIO and PAA


def test_no_aio_item_is_no_aio(load_fixture):
    body = copy.deepcopy(load_fixture(AIO))
    body["tasks"][0]["result"][0]["items"] = [i for i in items_of(body) if i["type"] != "ai_overview"]
    result = parse_response("q", body)
    assert result.status == STATUS_NO_AIO and not result.shown and len(result.organic) == 9


def test_aio_without_references_is_incomplete(load_fixture):
    result = parse_response("q", incomplete(load_fixture))
    assert result.status == STATUS_INCOMPLETE and result.shown and result.references == []


def test_task_error_is_failed_not_no_aio(load_fixture):
    body = copy.deepcopy(load_fixture(AIO))
    body["tasks"][0].update(status_code=40501, status_message="Invalid Field: 'location_code'.", result=None)
    result = parse_response("q", body)
    assert result.status == STATUS_FAILED and "40501" in result.error and "location_code" in result.error


def test_real_empty_tasks_error_names_the_envelope_status(load_fixture):
    result = parse_response("q", load_fixture(NO_TASKS))
    assert result.status == STATUS_FAILED
    assert result.error == "no tasks in response (status 20000 Ok.)"


def test_envelope_error_is_failed():
    result = parse_response("q", {"status_code": 40100, "status_message": "You are not authorized."})
    assert result.status == STATUS_FAILED and "not authorized" in result.error


def test_incomplete_retried_once_for_checks_but_not_probes(load_fixture):
    budget = CallBudget(10)
    provider, session = make([FakeResponse(body=incomplete(load_fixture)), FakeResponse(body=load_fixture(AIO))],
                             budget=budget)
    assert provider.fetch_ai_overview("q").status == STATUS_OK
    assert len(session.calls) == 2 and budget.used == 2

    provider, session = make([FakeResponse(body=incomplete(load_fixture))])
    assert provider.fetch_ai_overview("q", retry_incomplete=False).status == STATUS_INCOMPLETE
    assert len(session.calls) == 1


def test_retries_5xx_and_network_errors_then_succeeds(load_fixture):
    budget = CallBudget(10)
    provider, session = make([FakeResponse(503, text="busy"), requests.ConnectionError("reset"),
                              FakeResponse(body=load_fixture(AIO))], budget=budget)
    assert provider.fetch_ai_overview("q").status == STATUS_OK
    assert budget.used == 3


def test_bad_login_fails_without_retry():
    provider, session = make([FakeResponse(401, text="unauthorized")])
    result = provider.fetch_ai_overview("q")
    assert result.status == STATUS_FAILED and "HTTP 401" in result.error and len(session.calls) == 1


def test_get_provider_reads_credentials_from_env(monkeypatch):
    with pytest.raises(ValueError, match="DATAFORSEO_LOGIN"):
        get_provider("dataforseo")
    monkeypatch.setenv("DATAFORSEO_LOGIN", "me")
    monkeypatch.setenv("DATAFORSEO_PASSWORD", "pw")
    provider = get_provider("dataforseo")
    assert isinstance(provider, DataForSeoProvider) and provider.auth == ("me", "pw")


def task_error(load_fixture, code, message):
    body = copy.deepcopy(load_fixture(AIO))
    body["tasks"][0].update(status_code=code, status_message=message, result=None)
    return body


@pytest.mark.parametrize("code", [40101, 50000])
def test_transient_task_error_retried_once_and_counted(load_fixture, code, caplog):
    budget = CallBudget(10)
    provider, session = make([FakeResponse(body=task_error(load_fixture, code, "Internal SE Server Error.")),
                              FakeResponse(body=load_fixture(AIO))], budget=budget)
    assert provider.fetch_ai_overview("q").status == STATUS_OK
    assert len(session.calls) == 2 and budget.used == 2  # the retry is a billed call
    assert f"task status: {code}" in caplog.text and "retrying once" in caplog.text


def test_transient_error_retried_only_once(load_fixture):
    err = task_error(load_fixture, 40101, "Internal SE Server Error.")
    provider, session = make([FakeResponse(body=err), FakeResponse(body=copy.deepcopy(err))])
    result = provider.fetch_ai_overview("q", retry_incomplete=False)  # select probes retry transient errors too
    assert result.status == STATUS_FAILED and "40101" in result.error and len(session.calls) == 2


def test_permanent_task_error_not_retried(load_fixture):
    provider, session = make([FakeResponse(body=task_error(load_fixture, 40501, "Invalid Field."))])
    assert provider.fetch_ai_overview("q").status == STATUS_FAILED and len(session.calls) == 1


def test_no_retry_when_the_budget_is_used_up(load_fixture):
    budget = CallBudget(1)
    provider, session = make([FakeResponse(body=task_error(load_fixture, 40101, "Internal SE Server Error."))],
                             budget=budget)
    assert provider.fetch_ai_overview("q").status == STATUS_FAILED and len(session.calls) == 1


# Varsity blogs as in data/private/blogs.csv (titles and URLs are public)
REAL_BLOGS = [Blog("Forward Deployed Engineer Learning Path: How to Become an FDE in 2026",
                   "forward deployed engineer learning path",
                   "https://www.interviewbit.com/varsity/blog/forward-deployed-engineer-learning-path/", "fde"),
              Blog("Forward Deployed Engineer Skills: What AI Companies Actually Hire For in 2026",
                   "forward deployed engineer skills",
                   "https://www.interviewbit.com/varsity/blog/forward-deployed-engineer-skills/", "fde")]


def _hidden(result, source):
    return next(r for r in result.references if r.source == source and "google.com/goto" in r.url)


def _relabelled(body, title):
    """The real response, with its hidden Scaler link relabelled as an InterviewBit page titled `title`."""
    result = parse_response("help me evaluate the best online learning paths for forward deployed engineering", body)
    ref = _hidden(result, "Scaler")
    ref.source, ref.title = "InterviewBit", title
    return result, ref


def test_real_hidden_scaler_link_counts_as_a_scaler_page_by_label(load_fixture):
    q = "help me evaluate the best online learning paths for forward deployed engineering"
    result = parse_response(q, load_fixture(LEARNING_PATHS))
    ref = _hidden(result, "Scaler")
    assert ref.title.startswith("Forward Deployed Engineer Skills 2026")
    m = match_citations(result, REAL_BLOGS)
    assert [(p.url, p.position, p.site, p.match, p.name) for p in m.our_pages] == [
        ("", ref.position, "Scaler", "label", "Scaler page (URL hidden)")]
    # a Scaler label is never a Varsity blog, even with a blog-like title
    assert not m.varsity_cited and m.brand_cited and m.brand_position == ref.position and m.brand_urls == []
    assert "google.com" not in m.competing_domains and "fde.academy" in m.competing_domains


def test_real_hidden_interviewbit_link_is_a_blog_only_when_its_title_matches(load_fixture):
    body = load_fixture(LEARNING_PATHS)
    result, ref = _relabelled(body, "Forward Deployed Engineer Learning Path: How to Become an FDE ... - InterviewBit")
    m = match_citations(result, REAL_BLOGS)
    assert (m.varsity_cited, m.cited_url, m.position, m.varsity_match) == (
        True, REAL_BLOGS[0].url, ref.position, "title")
    assert [(p.url, p.match) for p in m.our_pages] == [(REAL_BLOGS[0].url, "title")]
    # the real Scaler title resembles the skills blog but isn't it: an InterviewBit page, not the blog
    result, ref = _relabelled(body, "Forward Deployed Engineer Skills 2026 | The Technical & Customer-Facing Skills You Need'")
    m = match_citations(result, REAL_BLOGS)
    assert not m.varsity_cited and m.brand_cited
    assert [(p.site, p.match, p.name) for p in m.our_pages] == [("InterviewBit", "label", "InterviewBit page (URL hidden)")]
    # without blogs to compare, a hidden InterviewBit link is a label match
    assert match_citations(result).our_pages[0].match == "label"
