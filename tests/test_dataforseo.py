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


MOBILE = "dataforseo/aio_fde_salary_india_mobile.json"
DESKTOP = "dataforseo/aio_fde_salary_india_desktop.json"
NO_TASKS = "dataforseo/no_tasks_ok_envelope.json"
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
