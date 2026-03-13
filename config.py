"""
ARGUS application configuration.
Single source of truth for shared settings.
"""

import os

# Web server port. Override at runtime with the PORT environment variable.
API_PORT: int = int(os.getenv("PORT", "5050"))
