"""Config routes for Solo Dev LLM Bench."""

from fastapi import APIRouter

from src.config_loader import load_config, save_config

router = APIRouter()


@router.get("/api/config")
async def get_config():
    """Return current configuration."""
    return load_config()


@router.post("/api/config")
async def update_config(config: dict):
    """Save updated configuration."""
    save_config(config)
    return {"status": "ok", "config": config}