import copy

import requests
import pytest

from tracker.checks import CallBudget, get_provider
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
    return DataForSeoProvider("login", "pw", session=session, backoff=0, incomplete_delay=0, **kw), session


AIO = "dataforseo/aio_fde_salary_india_mobile.json"
NO_AIO = "dataforseo/no_aio_best_fde_course_india.json"
TASK_ERROR = "dataforseo/task_error.json"


def incomplete(load_fixture):
    body = copy.deepcopy(load_fixture(AIO))
    aio = body["tasks"][0]["result"][0]["items"][0]
    aio["references"], aio["items"] = [], []
    return body


def test_request_body_and_auth(load_fixture):
    provider, session = make([FakeResponse(body=load_fixture(AIO))])
    provider.fetch_ai_overview("fde salary")
    call = session.calls[0]
    assert call["url"] == ENDPOINT and call["auth"] == ("login", "pw")
    assert call["json"] == [{"keyword": "fde salary", "location_code": 2356, "language_code": "en",
                             "device": "mobile", "load_async_ai_overview": True}]


def test_aio_references_in_order_with_nested_ones_after(load_fixture):
    result = parse_response("q", load_fixture(AIO))
    assert result.status == STATUS_OK and result.shown
    assert [domain_of(r.url) for r in result.references] == ["glassdoor.co.in", "ambitionbox.com",
                                                             "interviewbit.com", "palantir.com"]
    assert [r.position for r in result.references] == [1, 2, 3, 4]
    assert result.references[2].title == "Forward Deployed Engineer Salary in India & Career Path"


def test_varsity_citation_and_url_level_organic_rank(load_fixture):
    m = match_citations(parse_response("q", load_fixture(AIO)))
    assert m.varsity_cited and m.position == 3
    assert m.organic_rank == 2 and m.rank_match == "url"  # DataForSEO organic URLs are real, not redirects
    assert m.competing_domains == ["glassdoor.co.in", "ambitionbox.com", "palantir.com"]


def test_no_aio_item_is_no_aio(load_fixture):
    result = parse_response("q", load_fixture(NO_AIO))
    assert result.status == STATUS_NO_AIO and not result.shown
    assert [o.position for o in result.organic] == [1, 2]


def test_aio_without_references_is_incomplete(load_fixture):
    result = parse_response("q", incomplete(load_fixture))
    assert result.status == STATUS_INCOMPLETE and result.shown and result.references == []


def test_task_error_is_failed_not_no_aio(load_fixture):
    result = parse_response("q", load_fixture(TASK_ERROR))
    assert result.status == STATUS_FAILED and "40501" in result.error


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
