"""Models route for Solo Dev LLM Bench."""

import httpx
import logging
from fastapi import APIRouter, HTTPException

from src.config_loader import load_config
from src.benchmark import fetch_models

logger = logging.getLogger("solo_dev_llm_bench")

router = APIRouter()


@router.get("/api/models")
async def get_models():
    """Fetch LLM models from LM Studio native v1 API."""
    config = load_config()
    lm_studio_url = config.get("lm_studio_url", "http://localhost:1234").rstrip("/")
    try:
        models = await fetch_models(lm_studio_url)
        return {"models": models}
    except httpx.HTTPStatusError as e:
        logger.error(
            "HTTP error from LM Studio: %s %s — status %s",
            e.request.method, e.request.url, e.response.status_code,
        )
        raise HTTPException(status_code=502, detail=f"LM Studio HTTP {e.response.status_code}: {e.response.text[:500]}")
    except httpx.RequestError as e:
        logger.error(
            "Connection error reaching LM Studio at %s: %s", lm_studio_url, e
        )
        raise HTTPException(status_code=502, detail=f"Cannot connect to LM Studio at {lm_studio_url}: {e}")
    except Exception as e:
        logger.error(
            "Unexpected error fetching models from LM Studio: %s — %s", type(e).__name__, e
        )
        raise HTTPException(status_code=502, detail=f"Unexpected error: {e}")