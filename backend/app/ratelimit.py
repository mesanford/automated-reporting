"""Rate limiting via slowapi.

Keyed by Firebase user ID when an `X-User-Id` header is present, otherwise
by remote IP. In-memory storage is fine for Cloud Run with `min-instances=1`
and modest fan-out; if we ever scale wider, point `RATELIMIT_STORAGE_URL`
at Memorystore Redis.
"""

import os

from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address


def _key(request: Request) -> str:
    user_id = request.headers.get("X-User-Id")
    if user_id:
        return f"user:{user_id}"
    return f"ip:{get_remote_address(request)}"


limiter = Limiter(
    key_func=_key,
    storage_uri=os.getenv("RATELIMIT_STORAGE_URL", "memory://"),
    default_limits=[os.getenv("RATELIMIT_DEFAULT", "120/minute")],
    enabled=os.getenv("RATELIMIT_ENABLED", "1") != "0",
)
