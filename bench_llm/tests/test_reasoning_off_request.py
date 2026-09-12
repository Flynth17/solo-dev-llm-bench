"""Focused regression coverage for the canonical benchmark reasoning rule.

The benchmark engine must send request-level ``reasoning: "off"`` to LM Studio's
``/api/v1/chat`` endpoint so throughput/timing metrics are never polluted by
reasoning/thinking tokens -- regardless of the model's default (which the registry
may report as ``"on"``).

This test inspects the *outgoing* request payload captured from a mocked
``httpx.AsyncClient`` and asserts:
  - ``reasoning == "off"`` is present on every benchmark request, and
  - no other generation parameter is altered by that change.
"""

import asyncio
from unittest.mock import patch

import src.benchmark as bmod


class _ChatResp:
    """Canned /api/v1/chat response body with stats."""

    def __init__(self, stats):
        self._stats = stats

    def raise_for_status(self):
        pass

    def json(self):
        return {"stats": self._stats}


class _ChatClient:
    """Stand-in for httpx.AsyncClient in run_benchmark; captures the request body."""

    captured_payloads = []  # list of the json= payloads actually posted

    def __init__(self, responses):
        self._responses = list(responses)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None, **kwargs):
        _ChatClient.captured_payloads.append(dict(json))
        if self._responses:
            return self._responses.pop(0)
        return _ChatResp({})


def _stats(tps=50.0, ttft=0.2, inp=100, out=40):
    return {
        "tokens_per_second": tps,
        "time_to_first_token_seconds": ttft,
        "input_tokens": inp,
        "total_output_tokens": out,
        "model_load_time_seconds": None,
    }


class TestBenchmarkReasoningOffRequest:
    def test_outgoing_request_contains_reasoning_off(self):
        """The canonical benchmark request MUST carry reasoning=off."""
        _ChatClient.captured_payloads = []
        resp = _ChatResp(_stats())
        with patch("src.benchmark.httpx.AsyncClient",
                   return_value=_ChatClient([resp])):
            asyncio.run(
                bmod.run_benchmark(
                    lm_studio_url="http://localhost:1234",
                    model="ornith-1.5-35b-a3b",
                    prompt="hi",
                    iterations=1,
                    max_tokens=64,
                    temperature=0.0,
                )
            )

        assert _ChatClient.captured_payloads, "no benchmark request was posted"
        payload = _ChatClient.captured_payloads[-1]
        assert payload["reasoning"] == "off"

    def test_reasoning_off_not_altered_by_other_params(self):
        """Adding reasoning=off must not change any other generation parameter."""
        _ChatClient.captured_payloads = []
        resp = _ChatResp(_stats())
        with patch("src.benchmark.httpx.AsyncClient",
                   return_value=_ChatClient([resp])):
            asyncio.run(
                bmod.run_benchmark(
                    lm_studio_url="http://localhost:1234",
                    model="some-model-key",
                    prompt="a benchmark prompt",
                    iterations=1,
                    max_tokens=256,
                    temperature=0.7,
                )
            )

        payload = _ChatClient.captured_payloads[-1]
        # reasoning is explicitly off ...
        assert payload["reasoning"] == "off"
        # ... and every other generation parameter is exactly as supplied.
        assert payload["model"] == "some-model-key"
        assert payload["input"] == "a benchmark prompt"
        assert payload["temperature"] == 0.7
        assert payload["max_output_tokens"] == 256
        assert payload["stream"] is False
        assert payload["store"] is False

    def test_reasoning_off_sent_on_every_iteration(self):
        """reasoning=off is attached to each posted request, not just once."""
        _ChatClient.captured_payloads = []
        responses = [_ChatResp(_stats()), _ChatResp(_stats(tps=70.0))]
        with patch("src.benchmark.httpx.AsyncClient",
                   return_value=_ChatClient(responses)):
            result = asyncio.run(
                bmod.run_benchmark(
                    lm_studio_url="http://localhost:1234",
                    model="ornith-1.5-35b-a3b",
                    prompt="hi",
                    iterations=2,
                    max_tokens=64,
                    temperature=0.0,
                )
            )

        assert len(_ChatClient.captured_payloads) == 2
        for payload in _ChatClient.captured_payloads:
            assert payload["reasoning"] == "off"
        # Sanity: the run still produced one result row per iteration (no breakage).
        assert len(result["runs"]) == 2
