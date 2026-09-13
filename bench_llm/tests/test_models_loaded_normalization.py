"""Focused regression coverage for model ``loaded``-state normalisation (Act 17.1, FIX 1).

LM Studio exposes a loaded model via a non-empty ``loaded_instances`` list -- there is
no per-model top-level boolean. These tests pin that behaviour so unloaded models are
never falsely reported as loaded and vice-versa, plus preservation of useful metadata
and backward-compatible identity fields.

No network access: :class:`httpx.AsyncClient` is replaced with a fake returning canned
LM-Studio-style payloads.
"""

from unittest.mock import patch

import src.benchmark


# ----------------------------------------------------------------------
# Fake httpx client -- async context manager + awaited get() + json().
# ----------------------------------------------------------------------
class _FakeResp:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        return None

    def json(self):
        return self._data


class _FakeClient:
    """Async-context-manager fake client returning a fixed payload from get()."""

    def __init__(self, payload, *args, **kwargs):
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url):
        return _FakeResp(self._payload)


def _fetch_models(payload):
    """Return the normalized model list for *payload* (no live network access)."""
    import asyncio

    fake = _FakeClient(payload)
    with patch("src.benchmark.httpx.AsyncClient", lambda *a, **k: fake):

        async def _run():
            return await src.benchmark.fetch_models("http://localhost:1234")

        return asyncio.run(_run())


EMPTY_PAYLOAD = {
    "models": [
        {
            "key": "m-empty",
            "display_name": "M Empty",
            "type": "llm",
            "quantization": {"name": "Q4_K_M", "bits_per_weight": 4},
            "max_context_length": 131072,
        }
    ]
}


LOADED_PAYLOAD = {
    "models": [
        {
            "key": "ornith-1.5-35b-a3b",
            "display_name": "Ornith 1.5 35B A3B",
            "type": "llm",
            "quantization": {"name": "Q5_K_M", "bits_per_weight": 5},
            "max_context_length": 262144,
            "loaded_instances": [
                {
                    "id": "atomicchat/ornith-1.5-35b-a3b",
                    "config": {"context_length": 262144, "flash_attention": False},
                    # Unknown extra field must be stripped (only id/config preserved).
                    "unrelated_field": "drop-me",
                }
            ],
        }
    ]
}


# ----------------------------------------------------------------------
# Tests
# ----------------------------------------------------------------------

def test_empty_loaded_instances_reports_unloaded():
    """A model with no loaded instances is reported unloaded (the Act 17.1 bug)."""
    models = _fetch_models(EMPTY_PAYLOAD)

    assert len(models) == 1
    assert models[0]["key"] == "m-empty"
    assert models[0]["loaded"] is False
    assert models[0]["loaded_instances"] == []


def test_populated_loaded_instances_reports_loaded():
    """A model with a non-empty loaded_instances list is reported loaded."""
    models = _fetch_models(LOADED_PAYLOAD)

    assert len(models) == 1
    assert models[0]["key"] == "ornith-1.5-35b-a3b"
    assert models[0]["loaded"] is True


def test_loaded_instance_id_and_config_preserved():
    """Each loaded instance keeps only id + config LM Studio supplied."""
    models = _fetch_models(LOADED_PAYLOAD)

    instances = models[0]["loaded_instances"]
    assert len(instances) == 1
    assert instances[0]["id"] == "atomicchat/ornith-1.5-35b-a3b"
    assert instances[0]["config"] == {"context_length": 262144, "flash_attention": False}
    # Non-preserved fields are dropped.
    assert "unrelated_field" not in instances[0]


def test_max_context_length_preserved_and_optional():
    """max_context_length is surfaced when present and None when absent."""
    loaded = _fetch_models(LOADED_PAYLOAD)[0]
    empty = _fetch_models(EMPTY_PAYLOAD)[0]

    assert loaded["max_context_length"] == 262144
    # Absent in payload -> None (never fabricated).
    assert empty["max_context_length"] == 131072


def test_identity_fields_backward_compatible():
    """key / name / type / quantization remain unchanged for existing consumers."""
    m = _fetch_models(LOADED_PAYLOAD)[0]

    assert m["key"] == "ornith-1.5-35b-a3b"
    assert m["name"] == "Ornith 1.5 35B A3B"
    assert m["type"] == "llm"
    assert m["quantization"] == {"name": "Q5_K_M", "bits_per_weight": 5}


def test_non_llm_entries_are_filtered_out():
    """Only type=='llm' entries are normalised (unchanged contract)."""
    payload = {
        "models": [
            {"key": "audio-1", "display_name": "Audio", "type": "embedding"},
            {"key": "keep-me", "display_name": "Keep", "type": "llm"},
        ]
    }
    models = _fetch_models(payload)

    assert [m["key"] for m in models] == ["keep-me"]
