"""Verify Supabase Auth access tokens locally, against the project's JWKS.

No per-request call to Supabase: the public keys are fetched once and cached,
refetched after an hour or when a token names a key we have not seen (key
rotation), but at most once a minute, counting failed attempts too, so neither
a stream of forged `kid`s nor a Supabase outage turns us into a proxy that
hammers it. Inside that window a cached (even expired) key is still served.

Only asymmetric algorithms are accepted. Accepting the header's `alg` blindly
is the classic JWT confusion bug (HS256 signed with the public key); the
allow-list closes it, and the header `alg` must also be the one the token's own
key uses.

Two failure types, because the caller answers them differently: `AuthError`
means THIS token is no good (401, the browser signs out), `AuthUnavailable`
means we could not check any token (503, the browser must NOT sign out).
"""

from __future__ import annotations

import asyncio
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
        self._fetched_at: float | None = None  # last SUCCESSFUL fetch
        self._attempted_at: float | None = None  # last fetch attempt, ok or not
        self._last_failed = False
        self._lock: asyncio.Lock | None = None

    async def key(self, kid: str) -> jwt.PyJWK:
        # Lock created lazily: asyncio primitives bind to the loop that first
        # contends them, so not at import time. Concurrent callers queue behind
        # the in-flight fetch and then read ITS result via the checks below.
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            return await self._key_locked(kid)

    async def _key_locked(self, kid: str) -> jwt.PyJWK:
        now = self._clock()
        fresh = self._fetched_at is not None and now - self._fetched_at < _JWKS_TTL_S
        if kid in self._keys and fresh:
            return self._keys[kid]
        if self._attempted_at is not None and now - self._attempted_at < _MIN_REFRESH_S:
            # Refetch window closed: never hit Supabase again this soon, whether
            # the last attempt failed (outage) or succeeded (forged kid).
            if kid in self._keys:
                return self._keys[kid]  # a stale key beats refusing everyone
            if self._last_failed:
                raise AuthUnavailable("could not fetch signing keys")
            raise AuthError("unknown signing key")
        self._attempted_at = now
        try:
            await self._refresh()
        except Exception as exc:
            self._last_failed = True
            if kid in self._keys:
                return self._keys[kid]
            raise AuthUnavailable("could not fetch signing keys") from exc
        self._last_failed = False
        if kid not in self._keys:
            raise AuthError("unknown signing key")
        return self._keys[kid]

    async def _refresh(self) -> None:
        keyset = jwt.PyJWKSet.from_dict(await self._fetch(self._url))
        self._keys = {k.key_id: k for k in keyset.keys if k.key_id}
        self._fetched_at = self._clock()


# PyJWT rejects an `iat` ahead of our clock; a slightly slow host clock must
# not 401 a freshly signed-in user.
_CLOCK_LEEWAY_S = 30


async def verify_token(token: str, cache: JwksCache) -> Claims:
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise AuthError("malformed token") from exc
    kid, alg = header.get("kid"), header.get("alg")
    if not kid or alg not in ALGORITHMS:
        raise AuthError("unsupported token")
    key = await cache.key(kid)
    if alg != key.algorithm_name:
        raise AuthError("token algorithm does not match its key")
    try:
        payload = jwt.decode(
            token,
            key.key,
            algorithms=[alg],
            audience="authenticated",
            issuer=settings.supabase_issuer,
            leeway=_CLOCK_LEEWAY_S,
            options={"require": ["exp", "sub", "aud", "iss"]},
        )
    except (jwt.PyJWTError, TypeError, ValueError) as exc:
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
