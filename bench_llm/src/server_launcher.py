"""Server launcher for Solo Dev LLM Bench.

This script ensures the correct Python path is set before starting uvicorn,
avoiding the ModuleNotFoundError that can occur with CMD's set semantics.
"""

import os
import sys
from pathlib import Path

# Add the project directory to Python path so uvicorn can find src
PROJECT_DIR = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(PROJECT_DIR))

# Ensure data directory exists
DATA_DIR = PROJECT_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)


def main():
    """Start the uvicorn server."""
    import uvicorn

    print("Starting Solo Dev LLM Bench on http://127.0.0.1:8000")
    print("Press Ctrl+C to stop the server.")
    print()

    # Derive the application source directory from this file's location.
    # This restricts WatchFiles to only monitor src/*.py files,
    # preventing runtime-generated Python files (e.g., latest_output.py)
    # from triggering backend reloads.
    SRC_DIR = Path(__file__).resolve().parent
    sys.path.insert(0, str(PROJECT_DIR))

    uvicorn.run(
        "src.main:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
        reload_dirs=[str(SRC_DIR)],
    )


if __name__ == "__main__":
    main()