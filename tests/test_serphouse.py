import requests
import pytest

from tracker.checks import BudgetExceeded, CallBudget, get_provider
from tracker.checks.base import STATUS_FAILED, STATUS_NO_AIO, STATUS_OK
from tracker.checks.serphouse import ENDPOINT, SerphouseProvider, parse_response
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

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "json": json, "headers": headers})
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def make(responses, **kw):
    session = FakeSession(responses)
    return SerphouseProvider("k", session=session, backoff=0, **kw), session


AIO = "serphouse/aio_what_is_fde_mobile.json"
INCOMPLETE = "serphouse/incomplete_fde_salary_india_mobile.json"
NO_AIO = "serphouse/no_aio_best_fde_course_india.json"


def test_real_full_aio_parses_five_references_in_order(load_fixture):
    result = parse_response("what is a forward deployed engineer", load_fixture(AIO))
    assert result.status == STATUS_OK and result.shown
    domains = [domain_of(r.url) for r in result.references]
    assert domains == ["linkedin.com", "iit.edu", "posthog.com", "roadmap.sh", "nareshit.com"]
    assert [r.position for r in result.references] == [1, 2, 3, 4, 5]
    assert result.references[1].source == "Illinois Institute of Technology"
    assert "&#39;" not in result.references[0].title  # HTML entities decoded
    assert not match_citations(result).varsity_cited


def test_real_late_loading_aio_has_no_references(load_fixture):
    result = parse_response("forward deployed engineer salary in india", load_fixture(INCOMPLETE))
    assert result.shown and result.references == []
    assert len(result.organic) == 8


def test_real_no_aio(load_fixture):
    result = parse_response("best forward deployed engineer course in india", load_fixture(NO_AIO))
    assert result.status == STATUS_NO_AIO
    assert not result.shown and result.error is None
    assert [o.position for o in result.organic] == list(range(1, 10))
    assert result.organic[0].url.startswith("https://www.learnbay.co/")


def test_provider_error_body_fails():
    # Shape returned by the GET/gl/hl format we no longer use.
    body = {"status": "error", "msg": "Please try again", "error": "(426251028)check_html_manually"}
    result = parse_response("q", body)
    assert result.status == STATUS_FAILED and "check_html_manually" in result.error


@pytest.mark.parametrize("body", [{"status": "success"}, {"status": "success", "results": {"organic": []}}, []])
def test_parse_bad_bodies_fail(body):
    assert parse_response("q", body).status == STATUS_FAILED


def test_request_is_post_india_english_mobile(load_fixture):
    provider, session = make([FakeResponse(body=load_fixture("serphouse/no_aio_best_fde_course_india.json"))])
    provider.fetch_ai_overview("fde course fees")
    call = session.calls[0]
    assert call["url"] == ENDPOINT
    assert call["json"] == {"data": {"q": "fde course fees", "domain": "google.com", "loc": "India",
                                     "lang": "en", "device": "mobile", "serp_type": "web", "tbs": ""}}
    assert call["headers"]["Authorization"] == "Bearer k"


def test_request_always_clears_verbatim_mode(load_fixture):
    # Without "tbs": "" SERPHouse sends tbs=li:1 (Verbatim), which hides AI Overviews.
    provider, session = make([FakeResponse(body=load_fixture("serphouse/no_aio_best_fde_course_india.json"))])
    provider.fetch_ai_overview("q")
    data = session.calls[0]["json"]["data"]
    assert "tbs" in data, "tbs missing from request body: SERPHouse would default to Verbatim"
    assert data["tbs"] == ""


def test_retries_server_errors_then_succeeds(load_fixture):
    provider, session = make([
        FakeResponse(503, text="busy"),
        requests.ConnectionError("reset"),
        FakeResponse(body=load_fixture("serphouse/aio_what_is_fde_mobile.json")),
    ])
    assert provider.fetch_ai_overview("q").status == STATUS_OK
    assert len(session.calls) == 3


def test_client_error_not_retried():
    provider, session = make([FakeResponse(401, text="unauthorised")])
    result = provider.fetch_ai_overview("q")
    assert result.status == STATUS_FAILED and "401" in result.error
    assert len(session.calls) == 1


def test_non_json_response_fails():
    provider, _ = make([FakeResponse(200, body=None)])
    assert provider.fetch_ai_overview("q").status == STATUS_FAILED


def test_budget_counts_every_attempt_and_stops():
    budget = CallBudget(max_calls=2)
    provider, _ = make([FakeResponse(500), FakeResponse(500), FakeResponse(500)], budget=budget)
    with pytest.raises(BudgetExceeded):
        provider.fetch_ai_overview("q")
    assert budget.used == 2


def test_missing_key_rejected():
    with pytest.raises(ValueError):
        SerphouseProvider("")


def test_registry(monkeypatch):
    monkeypatch.setenv("SERP_PROVIDER", "fake")
    assert get_provider().name == "fake"
    with pytest.raises(ValueError):
        get_provider("nope")
