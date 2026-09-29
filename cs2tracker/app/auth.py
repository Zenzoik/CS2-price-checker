"""Validation of Telegram Mini App init data.

https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from urllib.parse import parse_qsl

# Init data is signed once, when the Mini App is opened, and anyone holding it
# acts as that user until it expires: keep the window short. An app left open
# longer asks to be reopened.
MAX_AGE = 6 * 3600


class AuthError(Exception):
    pass


def validate_init_data(init_data: str, bot_token: str, *, max_age: float = MAX_AGE,
                       now: float | None = None) -> dict:
    """Returns the Telegram user from signed init data, or raises AuthError."""
    if not init_data:
        raise AuthError("no init data")
    try:
        fields = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=True))
    except ValueError as e:
        raise AuthError("malformed init data") from e
    received = fields.pop("hash", "")
    if not re.fullmatch(r"[0-9a-f]{64}", received):
        raise AuthError("bad signature")
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received):
        raise AuthError("bad signature")

    try:
        auth_date = int(fields["auth_date"])
        user = json.loads(fields["user"])
        user_id = int(user["id"])
    except (KeyError, ValueError, TypeError) as e:
        raise AuthError("init data has no user") from e
    age = (time.time() if now is None else now) - auth_date
    if age > max_age:
        raise AuthError("init data expired")
    if age < -300:
        raise AuthError("init data from the future")
    return {**user, "id": user_id}


def sign_init_data(fields: dict[str, str], bot_token: str) -> str:
    """Test helper: builds init data the way Telegram does."""
    from urllib.parse import urlencode

    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode({**fields, "hash": digest})
