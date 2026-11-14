import json
from types import SimpleNamespace

from conftest import CATALOG

from cinebot.adapters.claude import ClaudeExplainer, build_user_prompt, parse_response
from cinebot.domain.models import Mode, TasteProfile


def test_parse_response_drops_bad_and_duplicate_indexes():
    raw = json.dumps(
        {
            "picks": [
                {"index": 2, "reason": "потому что", "vocab": [{"phrase": "binge-watch", "meaning_ru": "смотреть запоем"}]},
                {"index": 2, "reason": "дубль", "vocab": []},
                {"index": 99, "reason": "вне диапазона", "vocab": []},
                {"index": 0, "reason": "второй", "vocab": []},
            ]
        }
    )
    items = parse_response(raw, n_candidates=3, n=5)
    assert [i.index for i in items] == [2, 0]
    assert items[0].vocab[0].phrase == "binge-watch"


def test_parse_response_garbage_returns_none():
    assert parse_response("not json", 3, 3) is None
    assert parse_response('{"picks": []}', 3, 3) is None


def test_prompt_marks_catalog_text_as_data():
    p = build_user_prompt("Likes: drama", CATALOG[:2], ["a", "b"], Mode.ENGLISH, 2)
    assert "<candidate index=\"0\">" in p and "mode: english" in p


class _Beta:
    def __init__(self, response=None, exc=None):
        self._r, self._e = response, exc

    class messages:  # noqa: N801  (mimics client.beta.messages)
        pass


def _client(response):
    async def create(**kwargs):
        create.kwargs = kwargs
        return response

    client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=create)))
    return client, create


async def test_refusal_falls_back_to_none_and_request_shape():
    resp = SimpleNamespace(stop_reason="refusal", content=[])
    client, create = _client(resp)
    ex = ClaudeExplainer(client=client)
    out = await ex.rerank(TasteProfile(), "s", CATALOG[:2], ["a", "b"], Mode.TASTE, 1)
    assert out is None
    kw = create.kwargs
    assert kw["model"] == "claude-opus-5-5"
    assert kw["output_config"]["format"]["type"] == "json_schema"
    assert kw["fallbacks"] == "default" and "thinking" not in kw and "tool_choice" not in kw


async def test_success_path():
    text = json.dumps({"picks": [{"index": 1, "reason": "ok", "vocab": []}]})
    resp = SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=text)])
    client, _ = _client(resp)
    out = await ClaudeExplainer(client=client).rerank(TasteProfile(), "s", CATALOG[:2], ["a", "b"], Mode.TASTE, 1)
    assert out[0].index == 1
