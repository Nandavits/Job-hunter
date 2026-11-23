import httpx2

from jobhunter.llm import is_openrouter, make_client, openrouter_model

REPLY = {"id": "m", "type": "message", "role": "assistant", "model": "x", "stop_reason": "end_turn",
         "stop_sequence": None, "content": [{"type": "text", "text": "OK"}], "usage": {"input_tokens": 1, "output_tokens": 1}}


def _capture(client_factory):
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["x-api-key"] = request.headers.get("x-api-key")
        seen["model"] = __import__("json").loads(request.content)["model"]
        return httpx2.Response(200, json=REPLY)
    return seen, handler


def test_model_names_for_openrouter():
    assert openrouter_model("claude-sonnet-5-5") == "anthropic/claude-sonnet-5.5"
    assert openrouter_model("claude-opus-5-5") == "anthropic/claude-opus-5.5"
    assert openrouter_model("anthropic/claude-sonnet-5.5") == "anthropic/claude-sonnet-5.5"
    assert is_openrouter("sk-or-v1-abc") and not is_openrouter("sk-ant-abc")


def test_openrouter_key_goes_to_openrouter_with_bearer_token(monkeypatch):
    import anthropic
    seen, handler = _capture(None)
    real = anthropic.Anthropic

    def patched(**kw):
        return real(http_client=httpx2.Client(transport=httpx2.MockTransport(handler)), **kw)
    monkeypatch.setattr(anthropic, "Anthropic", patched)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-or-v1-secret")       # Job Hunter loads .env like this
    out = make_client("sk-or-v1-secret").messages.create(
        model="claude-sonnet-5-5", max_tokens=5, messages=[{"role": "user", "content": "hi"}])
    assert out.content[0].text == "OK"
    assert seen["url"] == "https://openrouter.ai/api/v1/messages"
    assert seen["auth"] == "Bearer sk-or-v1-secret"
    assert seen["model"] == "anthropic/claude-sonnet-5.5"


def test_anthropic_key_still_goes_to_anthropic(monkeypatch):
    import anthropic
    seen, handler = _capture(None)
    real = anthropic.Anthropic
    monkeypatch.setattr(anthropic, "Anthropic",
                        lambda **kw: real(http_client=httpx2.Client(transport=httpx2.MockTransport(handler)), **kw))
    make_client("sk-ant-abc").messages.create(model="claude-sonnet-5-5", max_tokens=5,
                                               messages=[{"role": "user", "content": "hi"}])
    assert seen["url"].startswith("https://api.anthropic.com/")
    assert "sk-ant-abc" in (seen["x-api-key"] or seen["auth"] or "")
    assert seen["model"] == "claude-sonnet-5-5"
