"""
ARGUS application configuration.
Single source of truth for shared settings.
"""

import os
from pathlib import Path

# Web server port. Override at runtime with the PORT environment variable.
API_PORT: int = int(os.getenv("PORT", "5050"))

# Base URL of the analysis API (used by bridge_manager and external callers).
ANALYZE_API_URL: str = os.getenv("ARGUS_ANALYZE_URL", f"http://localhost:{API_PORT}/api/analyze")

# Shared-storage directories for the Module-A → Bridge → Module-C pipeline.
_STORAGE_BASE = Path(os.getenv("ARGUS_STORAGE_DIR", "./shared_storage"))

RAW_DIR: Path       = _STORAGE_BASE / "raw"
ANALYZED_DIR: Path  = _STORAGE_BASE / "analyzed"
PROCESSED_DIR: Path = _STORAGE_BASE / "processed"
FAILED_DIR: Path    = _STORAGE_BASE / "failed"
