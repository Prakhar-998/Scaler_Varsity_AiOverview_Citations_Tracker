import pytest
import requests

from tracker.llm import AllLLMsFailed, LLMChain, LLMClient, LLMError, build_chain
from tracker.llm.openrouter import BASE_URL, OpenRouterClient


class ScriptedClient(LLMClient):
    """Fake model: each call pops the next scripted outcome (a string reply or an LLMError)."""
    provider = "fake"

    def __init__(self, model, outcomes):
        super().__init__(model)
        self.outcomes = list(outcomes)
        self.calls = 0

    def complete(self, prompt):
        self.calls += 1
        item = self.outcomes.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def chain(*clients, retries=2):
    return LLMChain(list(clients), max_retries=retries, backoff=0, sleep=lambda s: None)


def test_first_model_times_out_second_answers():
    a = ScriptedClient("a", [LLMError("openrouter timed out after 60s")])
    b = ScriptedClient("b", ['{"searches": ["x"]}'])
    c = chain(a, b)
    text, label = c.complete("p")
    assert label == "fake:b" and "searches" in text
    assert a.calls == 1  # a timeout is not retried on the same model
    assert c.dead == ["fake:a"]


def test_dead_model_skipped_on_later_calls():
    a = ScriptedClient("a", [LLMError("timeout")])
    b = ScriptedClient("b", ["one", "two"])
    c = chain(a, b)
    c.complete("p")
    assert c.complete("p") == ("two", "fake:b")
    assert a.calls == 1


def test_retryable_errors_retried_twice_then_next_model():
    a = ScriptedClient("a", [LLMError("429", retryable=True), LLMError("529", retryable=True),
                             LLMError("503", retryable=True)])
    b = ScriptedClient("b", ["ok"])
    sleeps = []
    c = LLMChain([a, b], max_retries=2, backoff=2, sleep=sleeps.append)
    assert c.complete("p") == ("ok", "fake:b")
    assert a.calls == 3 and sleeps == [2, 4]  # 1 try + 2 retries, exponential backoff


def test_retry_recovers_on_same_model():
    a = ScriptedClient("a", [LLMError("529", retryable=True), "ok"])
    assert chain(a).complete("p") == ("ok", "fake:a")


def test_all_fail_raises_and_then_fails_fast():
    a = ScriptedClient("a", [LLMError("timeout")])
    b = ScriptedClient("b", [LLMError("401")])
    c = chain(a, b)
    with pytest.raises(AllLLMsFailed):
        c.complete("p")
    with pytest.raises(AllLLMsFailed):  # no new calls once everything is dead
        c.complete("p")
    assert (a.calls, b.calls) == (1, 1) and c.available == []


def test_empty_chain_fails():
    with pytest.raises(AllLLMsFailed):
        chain().complete("p")


# --- OpenRouter client against a fake HTTP session ---

class Resp:
    def __init__(self, status=200, body=None, text=""):
        self.status_code, self._body, self.text = status, body, text

    def json(self):
        if self._body is None:
            raise ValueError
        return self._body


class Session:
    def __init__(self, item):
        self.item, self.calls = item, []

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        if isinstance(self.item, Exception):
            raise self.item
        return self.item


def openrouter(item):
    s = Session(item)
    return OpenRouterClient("k", "m:free", timeout=60, session=s), s


def test_openrouter_request_shape_and_reply():
    client, s = openrouter(Resp(body={"choices": [{"message": {"content": '{"searches": []}'}}]}))
    assert client.complete("hello") == '{"searches": []}'
    call = s.calls[0]
    assert call["url"] == f"{BASE_URL}/chat/completions"
    assert call["json"]["model"] == "m:free" and call["json"]["messages"][0]["content"] == "hello"
    assert call["headers"]["Authorization"] == "Bearer k"
    assert call["timeout"] == (10, 60)


@pytest.mark.parametrize("item,retryable", [
    (Resp(429, text="rate limited"), True),
    (Resp(529, text="overloaded"), True),
    (Resp(503, text="down"), True),
    (Resp(401, text="bad key"), False),
    (Resp(200, body={"error": {"code": 429, "message": "upstream rate limit"}}), True),
    (Resp(200, body={"choices": []}), False),
    (Resp(200, body={"choices": [{"message": {"content": "  "}}]}), False),
    (requests.ReadTimeout("slow"), False),
])
def test_openrouter_errors_classified(item, retryable):
    client, _ = openrouter(item)
    with pytest.raises(LLMError) as exc:
        client.complete("p")
    assert exc.value.retryable is retryable


def test_build_chain_reads_env_and_settings(monkeypatch):
    monkeypatch.delenv("OPENROUTER_KEY", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "alt-key")
    monkeypatch.setenv("OPENROUTER_MODELS", "a:free, b:free")
    settings = {"llm": {"chain": [{"provider": "openrouter"}, {"provider": "gemini", "enabled": False}],
                        "timeout_seconds": 60, "max_retries": 2}}
    c = build_chain(settings)
    assert [o.label for o in c.options] == ["openrouter:a:free", "openrouter:b:free"]
    assert c.options[0].api_key == "alt-key"


def test_build_chain_without_keys_is_empty(monkeypatch):
    for var in ("OPENROUTER_KEY", "OPENROUTER_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    c = build_chain({"llm": {"chain": [{"provider": "openrouter"}, {"provider": "gemini"}]}})
    assert c.options == []
