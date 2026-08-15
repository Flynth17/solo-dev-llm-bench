"""Prompts routes for Solo Dev LLM Bench."""

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException

router = APIRouter()

# Prompt-specific state and helpers (moved from main.py)

_PROMPTS_PATH = Path(__file__).parent.parent.parent / "data" / "prompts.json"

_DEFAULT_PROMPTS = [
    {
        "name": "500 Token General Benchmark",
        "prompt": "Write a short story about a robot learning to feel emotions."
    }
]


def _load_prompts() -> list:
    """Load saved prompts from disk, or return defaults if file doesn't exist."""
    if _PROMPTS_PATH.exists():
        try:
            data = json.loads(_PROMPTS_PATH.read_text(encoding="utf-8"))
            return data.get("prompts", _DEFAULT_PROMPTS)
        except (json.JSONDecodeError, KeyError):
            return list(_DEFAULT_PROMPTS)
    return list(_DEFAULT_PROMPTS)


def _save_prompts(prompts: list) -> None:
    """Save prompts list to disk."""
    _PROMPTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _PROMPTS_PATH.write_text(json.dumps({"prompts": prompts}, indent=2, ensure_ascii=False), encoding="utf-8")


# Initialize prompts cache
_prompts_cache = _load_prompts()


@router.get("/api/prompts")
async def get_prompts():
    """Return all saved prompts."""
    global _prompts_cache
    _prompts_cache = _load_prompts()
    return {"prompts": _prompts_cache}


@router.post("/api/prompts")
async def create_prompt(body: dict):
    """Save a new prompt."""
    global _prompts_cache
    name = (body.get("name") or "").strip()
    prompt_text = body.get("prompt") or ""
    if not name:
        raise HTTPException(status_code=400, detail="Prompt name cannot be empty")
    _prompts_cache = _load_prompts()
    # Check for duplicates (case-insensitive)
    for p in _prompts_cache:
        if p["name"].lower() == name.lower():
            raise HTTPException(status_code=409, detail=f"A prompt with the name '{name}' already exists")
    _prompts_cache.append({"name": name, "prompt": prompt_text})
    _save_prompts(_prompts_cache)
    return {"status": "ok", "prompts": _prompts_cache}


@router.put("/api/prompts/{name}")
async def update_prompt(name: str, body: dict):
    """Rename an existing prompt."""
    global _prompts_cache
    old_name = name  # URL param is the old name
    new_name = (body.get("name") or "").strip()
    if not new_name:
        raise HTTPException(status_code=400, detail="Prompt name cannot be empty")
    _prompts_cache = _load_prompts()
    found = False
    for i, p in enumerate(_prompts_cache):
        if p["name"] == old_name:
            # Check duplicate with new name
            for j, q in enumerate(_prompts_cache):
                if j != i and q["name"].lower() == new_name.lower():
                    raise HTTPException(status_code=409, detail=f"A prompt with the name '{new_name}' already exists")
            _prompts_cache[i]["name"] = new_name
            found = True
            break
    if not found:
        raise HTTPException(status_code=404, detail=f"Prompt '{old_name}' not found")
    _save_prompts(_prompts_cache)
    return {"status": "ok", "prompts": _prompts_cache}


@router.delete("/api/prompts/{name}")
async def delete_prompt(name: str):
    """Delete a prompt by name."""
    global _prompts_cache
    _prompts_cache = _load_prompts()
    original_len = len(_prompts_cache)
    _prompts_cache = [p for p in _prompts_cache if p["name"] != name]
    if len(_prompts_cache) == original_len:
        raise HTTPException(status_code=404, detail=f"Prompt '{name}' not found")
    _save_prompts(_prompts_cache)
    return {"status": "ok"}