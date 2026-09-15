"""Config routes for Solo Dev LLM Bench."""

from fastapi import APIRouter, HTTPException

from src.config_loader import load_config, save_config
from src.backend_url_policy import validate_backend_url, resolve_allowed_hosts, BackendUrlPolicyError

router = APIRouter()


@router.get("/api/config")
async def get_config():
    """Return current configuration."""
    return load_config()


@router.post("/api/config")
async def update_config(config: dict):
    """Save updated configuration.

    ``lm_studio_url`` is validated against the backend allow-list (loopback-only by
    default; extends via ``trusted_backend_hosts`` in config) before it is persisted,
    so an unsafe destination cannot become the configured benchmark backend and then
    be contacted by later model / benchmark requests.
    """
    lm_studio_url = config.get("lm_studio_url")
    if isinstance(lm_studio_url, str):
        try:
            validate_backend_url(lm_studio_url, resolve_allowed_hosts())
        except BackendUrlPolicyError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid backend URL: {exc}") from exc
    save_config(config)
    return {"status": "ok", "config": config}