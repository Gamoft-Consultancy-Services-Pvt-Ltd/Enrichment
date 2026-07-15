"""LEAD-52-S8 — Tests for the rating fallback client.

Uses fake Sonnet clients (no network) to exercise the success path, JSON
cleaning, schema validation, retry/backoff, and hard-failure behaviour.
"""

import json

import pytest

from rating_client import (
    RatingClient,
    RatingClientError,
    RatingRequest,
    RatingResponse,
    SkippedSignalContext,
)


# ---------------------------------------------------------------------------
# Fakes that mimic the Anthropic SDK shape (resp.content[i].text)
# ---------------------------------------------------------------------------

class _Block:
    def __init__(self, text):
        self.text = text


class _Resp:
    def __init__(self, text):
        self.content = [_Block(text)]


class FakeClient:
    """Returns a scripted sequence of outputs; raises if an item is an Exception."""

    def __init__(self, outputs):
        self._outputs = list(outputs)
        self.calls = 0
        self.messages = self  # so client.messages.create works

    def create(self, **kwargs):
        item = self._outputs[min(self.calls, len(self._outputs) - 1)]
        self.calls += 1
        if isinstance(item, Exception):
            raise item
        return _Resp(item)


def make_request(n=2) -> RatingRequest:
    sigs = [
        SkippedSignalContext(signal_id=f"sig_{i}", dimension="intent",
                             observation=f"observation {i}")
        for i in range(n)
    ]
    return RatingRequest(
        lead_id="L-1", tenant_id="t-1",
        lead_summary="Mid-size SaaS company, recent pricing-page activity.",
        skipped_signals=sigs,
    )


def good_json(signal_ids, fire=True):
    return json.dumps({
        "estimates": [
            {"signal_id": sid, "would_fire": fire, "rationale": "ctx"}
            for sid in signal_ids
        ],
        "overall_reasoning": "estimated from summary",
    })


# ---------------------------------------------------------------------------
# Success path
# ---------------------------------------------------------------------------

class TestSuccess:
    def test_valid_response_parsed(self):
        client = RatingClient(FakeClient([good_json(["sig_0", "sig_1"])]),
                              sleep=lambda *_: None)
        resp = client.estimate(make_request())
        assert isinstance(resp, RatingResponse)
        assert {e.signal_id for e in resp.estimates} == {"sig_0", "sig_1"}
        assert all(e.would_fire for e in resp.estimates)

    def test_no_skipped_signals_short_circuits(self):
        fake = FakeClient(["should not be called"])
        client = RatingClient(fake, sleep=lambda *_: None)
        req = RatingRequest(lead_id="L-1", tenant_id="t-1",
                            lead_summary="x", skipped_signals=[])
        resp = client.estimate(req)
        assert resp.estimates == []
        assert fake.calls == 0  # no API call made

    def test_json_fences_are_tolerated(self):
        fenced = "```json\n" + good_json(["sig_0", "sig_1"]) + "\n```"
        client = RatingClient(FakeClient([fenced]), sleep=lambda *_: None)
        resp = client.estimate(make_request())
        assert len(resp.estimates) == 2

    def test_unrequested_signals_are_filtered_out(self):
        # model hallucinates an extra signal id we never asked about
        payload = good_json(["sig_0", "sig_1", "ghost_signal"])
        client = RatingClient(FakeClient([payload]), sleep=lambda *_: None)
        resp = client.estimate(make_request())
        assert {e.signal_id for e in resp.estimates} == {"sig_0", "sig_1"}


# ---------------------------------------------------------------------------
# Retry + failure behaviour
# ---------------------------------------------------------------------------

class TestRetryAndFailure:
    def test_retries_then_succeeds(self):
        # first call raises, second returns valid JSON
        client = RatingClient(
            FakeClient([RuntimeError("transient"), good_json(["sig_0", "sig_1"])]),
            sleep=lambda *_: None,
        )
        resp = client.estimate(make_request())
        assert len(resp.estimates) == 2

    def test_hard_failure_raises_rating_client_error(self):
        client = RatingClient(
            FakeClient([RuntimeError("down"), RuntimeError("down"),
                        RuntimeError("down")]),
            max_retries=2, sleep=lambda *_: None,
        )
        with pytest.raises(RatingClientError):
            client.estimate(make_request())

    def test_non_json_response_raises_after_retries(self):
        client = RatingClient(
            FakeClient(["not json at all"]), max_retries=0, sleep=lambda *_: None,
        )
        with pytest.raises(RatingClientError):
            client.estimate(make_request())

    def test_schema_invalid_response_raises(self):
        bad = json.dumps({"estimates": [{"signal_id": "sig_0"}]})  # missing would_fire
        client = RatingClient(FakeClient([bad]), max_retries=0, sleep=lambda *_: None)
        with pytest.raises(RatingClientError):
            client.estimate(make_request())

    def test_empty_content_raises(self):
        class EmptyResp:
            content = []

        class EmptyClient:
            def __init__(self): self.messages = self
            def create(self, **k): return EmptyResp()

        client = RatingClient(EmptyClient(), max_retries=0, sleep=lambda *_: None)
        with pytest.raises(RatingClientError):
            client.estimate(make_request())
