"""Focused regression tests for quantization failure semantics (Act 1).

Ensures the five resolution states are never conflated: a real value, genuinely
unavailable metadata, malformed metadata, an unknown model key, and a
provider/lookup failure each map to a distinct, non-fabricated persisted value.

Uses asyncio.run() in sync test methods so no pytest-asyncio configuration is
required. fetch_models is mocked so the suite performs no network I/O.
"""

import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.benchmark import (  # noqa: E402
    QUANT_ABSENT,
    QUANT_ERROR,
    QUANT_FOUND,
    QUANT_MALFORMED,
    QUANT_NOT_FOUND,
    _resolve_quantization_status,
    persisted_quantization,
    resolve_persisted_quantization,
)


def _run(coro):
    return asyncio.run(coro)


class TestPersistedQuantizationMapping(unittest.TestCase):
    """Pure status -> persisted-value mapping (no I/O)."""

    def test_found_value_verbatim(self):
        from src.benchmark import QuantizationStatus as QS

        self.assertEqual(persisted_quantization(QS(QUANT_FOUND, value="Q4_K_M")), "Q4_K_M")

    def test_absent_label(self):
        from src.benchmark import QuantizationStatus as QS

        self.assertEqual(
            persisted_quantization(QS(QUANT_ABSENT)), "metadata_absent"
        )

    def test_malformed_label(self):
        from src.benchmark import QuantizationStatus as QS

        self.assertEqual(
            persisted_quantization(QS(QUANT_MALFORMED)), "metadata_malformed"
        )

    def test_not_found_label(self):
        from src.benchmark import QuantizationStatus as QS

        self.assertEqual(
            persisted_quantization(QS(QUANT_NOT_FOUND)), "model_not_found"
        )

    def test_error_label(self):
        from src.benchmark import QuantizationStatus as QS

        self.assertEqual(persisted_quantization(QS(QUANT_ERROR)), "lookup_failed")


class TestResolveQuantizationStatus(unittest.TestCase):
    """End-to-end persistence mapping through the async resolver (mocked fetch)."""

    KEY = "llm/Qwen2.5-7B-Instruct"

    def test_found_string_value(self):
        models = [{"key": self.KEY, "quantization": "Q4_K_M"}]
        with patch("src.benchmark.fetch_models", new=AsyncMock(return_value=models)):
            status = _run(resolve_persisted_quantization("http://x", self.KEY))
        self.assertEqual(status, "Q4_K_M")

    def test_found_dict_with_name(self):
        models = [{"key": self.KEY, "quantization": {"name": "Q5_K_M"}}]
        with patch("src.benchmark.fetch_models", new=AsyncMock(return_value=models)):
            status = _run(resolve_persisted_quantization("http://x", self.KEY))
        self.assertEqual(status, "Q5_K_M")

    def test_absent_metadata_when_quantization_empty(self):
        models = [{"key": self.KEY, "quantization": ""}]
        with patch("src.benchmark.fetch_models", new=AsyncMock(return_value=models)):
            status = _run(_resolve_quantization_status("http://x", self.KEY))
        self.assertEqual(status.status, QUANT_ABSENT)
        self.assertEqual(persisted_quantization(status), "metadata_absent")

    def test_malformed_when_dict_has_no_name(self):
        models = [{"key": self.KEY, "quantization": {}}]
        with patch("src.benchmark.fetch_models", new=AsyncMock(return_value=models)):
            status = _run(_resolve_quantization_status("http://x", self.KEY))
        self.assertEqual(status.status, QUANT_MALFORMED)
        self.assertEqual(persisted_quantization(status), "metadata_malformed")

    def test_malformed_when_wrong_type(self):
        models = [{"key": self.KEY, "quantization": 12345}]
        with patch("src.benchmark.fetch_models", new=AsyncMock(return_value=models)):
            status = _run(_resolve_quantization_status("http://x", self.KEY))
        self.assertEqual(status.status, QUANT_MALFORMED)
        self.assertEqual(persisted_quantization(status), "metadata_malformed")

    def test_not_found_when_key_absent(self):
        models = [{"key": "some/other-model", "quantization": "Q8_0"}]
        with patch("src.benchmark.fetch_models", new=AsyncMock(return_value=models)):
            status = _run(_resolve_quantization_status("http://x", self.KEY))
        self.assertEqual(status.status, QUANT_NOT_FOUND)
        self.assertEqual(persisted_quantization(status), "model_not_found")

    def test_error_when_provider_unreachable(self):
        import httpx

        async def boom(*args, **kwargs):
            raise httpx.TimeoutException("LM Studio unreachable")

        with patch("src.benchmark.fetch_models", new=AsyncMock(side_effect=boom)):
            status = _run(_resolve_quantization_status("http://x", self.KEY))
        self.assertEqual(status.status, QUANT_ERROR)
        self.assertEqual(persisted_quantization(status), "lookup_failed")

    def test_error_when_registry_malformed_json(self):
        async def boom(*args, **kwargs):
            raise ValueError("JSON decode failed")

        with patch("src.benchmark.fetch_models", new=AsyncMock(side_effect=boom)):
            status = _run(_resolve_quantization_status("http://x", self.KEY))
        self.assertEqual(status.status, QUANT_ERROR)
        self.assertEqual(persisted_quantization(status), "lookup_failed")


if __name__ == "__main__":
    unittest.main()
