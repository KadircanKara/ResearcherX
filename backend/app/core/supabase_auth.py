"""Verify Supabase Auth access tokens locally, against the project's JWKS.

No per-request call to Supabase: the public keys are fetched once and cached,
refetched after an hour or when a token names a key we have not seen (key
rotation), but at most once a minute so a stream of forged `kid`s cannot turn
us into a proxy that hammers Supabase.

Only asymmetric algorithms are accepted. Accepting the header's `alg` blindly
is the classic JWT confusion bug (HS256 signed with the public key); the
allow-list closes it.

Two failure types, because the caller answers them differently: `AuthError`
means THIS token is no good (401, the browser signs out), `AuthUnavailable`
means we could not check any token (503, the browser must NOT sign out).
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx
import jwt

from app.core.config import settings

ALGORITHMS = ("ES256", "RS256")
_JWKS_TTL_S = 3600.0
_MIN_REFRESH_S = 60.0


class AuthError(Exception):
    """The token is missing, malformed, expired, or not issued for us."""


class AuthUnavailable(Exception):
    """The signing keys could not be fetched and none are cached."""


@dataclass(frozen=True)
class Claims:
    sub: str
    email: str  # lower-cased


async def _fetch_jwks(url: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=5.0) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.json()


class JwksCache:
    def __init__(
        self,
        url: str,
        fetch: Callable[[str], Awaitable[dict[str, Any]]] = _fetch_jwks,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._url = url
        self._fetch = fetch
        self._clock = clock
        self._keys: dict[str, jwt.PyJWK] = {}
        self._fetched_at: float | None = None

    async def key(self, kid: str) -> jwt.PyJWK:
        age = None if self._fetched_at is None else self._clock() - self._fetched_at
        if kid in self._keys and age is not None and age < _JWKS_TTL_S:
            return self._keys[kid]
        if age is not None and age < _MIN_REFRESH_S:
            raise AuthError("unknown signing key")
        try:
            await self._refresh()
        except Exception as exc:
            if kid in self._keys:
                return self._keys[kid]  # a stale key beats refusing everyone
            raise AuthUnavailable("could not fetch signing keys") from exc
        if kid not in self._keys:
            raise AuthError("unknown signing key")
        return self._keys[kid]

    async def _refresh(self) -> None:
        keyset = jwt.PyJWKSet.from_dict(await self._fetch(self._url))
        self._keys = {k.key_id: k for k in keyset.keys if k.key_id}
        self._fetched_at = self._clock()


async def verify_token(token: str, cache: JwksCache) -> Claims:
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise AuthError("malformed token") from exc
    kid, alg = header.get("kid"), header.get("alg")
    if not kid or alg not in ALGORITHMS:
        raise AuthError("unsupported token")
    key = await cache.key(kid)
    try:
        payload = jwt.decode(
            token,
            key.key,
            algorithms=[alg],
            audience="authenticated",
            issuer=settings.supabase_issuer,
            options={"require": ["exp", "sub", "aud", "iss"]},
        )
    except jwt.PyJWTError as exc:
        raise AuthError(type(exc).__name__) from exc
    email = payload.get("email")
    if not isinstance(email, str) or not email:
        raise AuthError("token has no email")
    return Claims(sub=str(payload["sub"]), email=email.lower())


_cache: JwksCache | None = None


def jwks_cache() -> JwksCache:
    """One cache per process (single uvicorn worker, like the rate limiter)."""
    global _cache
    if _cache is None:
        _cache = JwksCache(settings.supabase_jwks_url)
    return _cache
