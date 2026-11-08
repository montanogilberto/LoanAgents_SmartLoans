"""
Signed call context — the only way trusted values reach a voice call.

smartloans_backend places the outbound call and passes this token to Twilio as
a <Stream> <Parameter> (Twilio relays it in the "start" frame's
customParameters — never in the URL). The token carries companyId, branchId,
clientId, orderId and the order summary the agent may say aloud. It is signed
with VOICE_CALL_SECRET (HMAC-SHA256, shared with the backend), so a caller who
reaches /voice/twilio-stream can't pick another company's data, and it expires
so an old token can't be replayed.

Format: base64url(json payload) "." base64url(hmac)
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

MAX_TTL_S = 15 * 60  # a token is minted right before Twilio dials
# The POS "mostrador" placeholder for anonymous sales (smartloans_backend
# notificationDispatch.WALK_IN_CLIENT_ID). It has a real phone on file, so a call
# for it would ring that number on every walk-in order — never call it.
WALK_IN_CLIENT_ID = 1


class InvalidCallToken(Exception):
    pass


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _signature(secret: str, body: str) -> str:
    return _b64(hmac.new(secret.encode(), body.encode("ascii"), hashlib.sha256).digest())


def sign_call_token(payload: dict, secret: str, ttl_s: int = 10 * 60, now: float | None = None) -> str:
    """Mints a token (the backend's job; also used by tests and the dev test-call script)."""
    if not secret:
        raise ValueError("VOICE_CALL_SECRET is not set")
    issued = int(now if now is not None else time.time())
    body = _b64(json.dumps({**payload, "exp": issued + min(ttl_s, MAX_TTL_S)},
                           separators=(",", ":"), ensure_ascii=False).encode())
    return f"{body}.{_signature(secret, body)}"


def verify_call_token(token: str, secret: str, now: float | None = None) -> dict:
    """Returns the payload, or raises InvalidCallToken (bad format, signature or expired)."""
    if not secret:
        raise InvalidCallToken("VOICE_CALL_SECRET is not set")
    try:
        body, sig = (token or "").split(".")
    except ValueError as exc:
        raise InvalidCallToken("malformed token") from exc
    if not hmac.compare_digest(sig, _signature(secret, body)):
        raise InvalidCallToken("bad signature")
    try:
        payload = json.loads(_unb64(body))
    except ValueError as exc:
        raise InvalidCallToken("malformed payload") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("exp"), int):
        raise InvalidCallToken("malformed payload")
    if payload["exp"] < (now if now is not None else time.time()):
        raise InvalidCallToken("expired")
    for key in ("companyId", "clientId"):
        if not isinstance(payload.get(key), int) or isinstance(payload.get(key), bool):
            raise InvalidCallToken(f"{key} missing")
    if payload["clientId"] == WALK_IN_CLIENT_ID:
        raise InvalidCallToken("walk-in client")
    return payload
