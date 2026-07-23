from __future__ import annotations

import json
from datetime import date

from finance_bot.llm import parse_message_with_llm


class _FakeResponse:
    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(
            {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "action": "transaction",
                                    "type": "expense",
                                    "amount": 1200,
                                    "category": "кафе",
                                }
                            )
                        }
                    }
                ]
            }
        ).encode("utf-8")


def test_llm_parser_converts_free_form_expense(monkeypatch) -> None:
    def fake_urlopen(request, timeout):
        assert timeout == 8.0
        body = json.loads(request.data.decode("utf-8"))
        assert body["model"] == "Hermes-4-70B"
        assert body["response_format"] == {"type": "json_object"}
        assert body["messages"][-1]["content"] == "вчера в кафе оставил 1200"
        return _FakeResponse()

    monkeypatch.setenv("NOUS_API_KEY", "test-key")
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    parsed = parse_message_with_llm("вчера в кафе оставил 1200")

    assert parsed.action == "transaction"
    assert parsed.type == "expense"
    assert parsed.amount == 1200
    assert parsed.category == "кафе"
    assert parsed.note == "вчера в кафе оставил 1200"


def test_llm_parser_validates_subscription_date(monkeypatch) -> None:
    class SubscriptionResponse(_FakeResponse):
        def read(self) -> bytes:
            return json.dumps(
                {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "action": "subscription",
                                        "name": "Spotify",
                                        "amount": "399,50",
                                        "next_payment_date": "2026-08-01",
                                    }
                                )
                            }
                        }
                    ]
                }
            ).encode("utf-8")

    monkeypatch.setenv("NOUS_API_KEY", "test-key")
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout: SubscriptionResponse(),
    )

    parsed = parse_message_with_llm("spotify снимет 399,50 первого августа")

    assert parsed.action == "subscription"
    assert parsed.amount == 399.50
    assert parsed.name == "Spotify"
    assert parsed.next_payment_date == date(2026, 8, 1)
