"""Supabase access-token verification: signature, claims, and the JWKS cache.

Keys are generated locally; the JWKS fetch is injected, so nothing touches the
network.
"""

import json
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from app.core.supabase_auth import (
    AuthError,
    AuthUnavailable,
    JwksCache,
    verify_token,
)

ISS = "https://abc.supabase.co/auth/v1"


@pytest.fixture(autouse=True)
def _supabase_settings(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "supabase_url", "https://abc.supabase.co")


def _keypair(kid: str = "k1"):
    priv = ec.generate_private_key(ec.SECP256R1())
    jwk = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(priv.public_key()))
    jwk.update(kid=kid, alg="ES256", use="sig")
    return priv, jwk


def _token(priv, kid="k1", **overrides):
    claims = {
        "sub": "user-1",
        "email": "Client@Acme.com",
        "aud": "authenticated",
        "iss": ISS,
        "exp": int(time.time()) + 3600,
    }
    claims.update(overrides)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, priv, algorithm="ES256", headers={"kid": kid})


def _cache(*jwks, calls=None, fail=False, clock=None):
    async def fetch(url):
        if calls is not None:
            calls.append(url)
        if fail:
            raise RuntimeError("down")
        return {"keys": list(jwks)}

    return JwksCache(
        "https://abc.supabase.co/auth/v1/.well-known/jwks.json",
        fetch=fetch,
        clock=clock or time.monotonic,
    )


async def test_a_valid_token_yields_sub_and_lowercased_email():
    priv, jwk = _keypair()
    claims = await verify_token(_token(priv), _cache(jwk))
    assert claims.sub == "user-1"
    assert claims.email == "client@acme.com"


@pytest.mark.parametrize(
    "overrides",
    [
        {"exp": int(time.time()) - 10},
        {"aud": "anon"},
        {"iss": "https://evil.example/auth/v1"},
        {"sub": None},
        {"email": None},
    ],
)
async def test_bad_claims_are_rejected(overrides):
    priv, jwk = _keypair()
    with pytest.raises(AuthError):
        await verify_token(_token(priv, **overrides), _cache(jwk))


async def test_a_token_signed_by_another_key_is_rejected():
    priv, jwk = _keypair()
    other, _ = _keypair()
    with pytest.raises(AuthError):
        await verify_token(_token(other), _cache(jwk))


async def test_garbage_and_hs256_are_rejected():
    priv, jwk = _keypair()
    with pytest.raises(AuthError):
        await verify_token("not-a-jwt", _cache(jwk))
    hs = jwt.encode({"sub": "x"}, "secret", algorithm="HS256", headers={"kid": "k1"})
    with pytest.raises(AuthError):
        await verify_token(hs, _cache(jwk))


async def test_keys_are_cached_between_calls():
    priv, jwk = _keypair()
    calls: list[str] = []
    cache = _cache(jwk, calls=calls)
    await verify_token(_token(priv), cache)
    await verify_token(_token(priv), cache)
    assert len(calls) == 1


async def test_unknown_kid_refetches_at_most_once_a_minute():
    priv, jwk = _keypair("k1")
    now = [1000.0]
    calls: list[str] = []
    cache = _cache(jwk, calls=calls, clock=lambda: now[0])
    await verify_token(_token(priv), cache)  # fetch 1
    rotated, _ = _keypair("k2")
    with pytest.raises(AuthError):
        await verify_token(_token(rotated, kid="k2"), cache)  # within 60s: no fetch
    assert len(calls) == 1
    now[0] += 61
    with pytest.raises(AuthError):
        await verify_token(_token(rotated, kid="k2"), cache)  # refetch, still unknown
    assert len(calls) == 2


async def test_jwks_unreachable_with_nothing_cached_is_unavailable_not_invalid():
    priv, jwk = _keypair()
    with pytest.raises(AuthUnavailable):
        await verify_token(_token(priv), _cache(jwk, fail=True))
