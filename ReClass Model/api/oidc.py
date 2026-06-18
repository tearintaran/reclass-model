"""Asymmetric (OIDC / JWKS) bearer-token validation — RS256, dependency-free.

The base auth path self-issues HS256 tokens and accepts static API keys (``api/auth.py``).
For real deployments behind an identity provider (Okta, Entra ID, Auth0, Keycloak, ...)
tokens are signed with an *asymmetric* key and the service validates them against the
IdP's published **JWKS** (JSON Web Key Set), checking issuer / audience / expiry and
honouring key rotation by ``kid``. This module implements exactly that for **RS256**
(RSA PKCS#1 v1.5 over SHA-256) using only the standard library — no ``cryptography`` /
``PyJWT`` dependency — so it runs in the same minimal, offline-testable environment as
the rest of the engine:

  * :func:`verify_rs256`     -- RSASSA-PKCS1-v1_5 signature verification from a raw
                               ``(n, e)`` public key (modular exponentiation + a
                               constant-time compare of the EMSA-PKCS1-v1_5 encoding),
  * :class:`JWKSClient`      -- a JWKS key set (static dict for tests/pinned keys, or a
                               lazily-fetched + TTL-cached URL) with ``kid`` lookup and
                               rotation-aware refetch,
  * :func:`decode_and_verify`-- full token validation: header ``alg``/``kid`` -> key ->
                               signature -> ``iss`` / ``aud`` / ``exp`` / ``nbf``.

ES256 (ECDSA P-256) is intentionally NOT implemented here: elliptic-curve verification
needs either a vetted EC implementation or the optional ``cryptography`` extra, and a
hand-rolled one would be a security risk. :func:`decode_and_verify` raises a clear
``UnsupportedAlgorithm`` for ES256/other algs rather than silently accepting them.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, Sequence


class UnsupportedAlgorithm(ValueError):
    """Raised for a JWT whose ``alg`` this verifier does not implement (e.g. ES256)."""


#: ASN.1 DigestInfo prefix for SHA-256 (RFC 8017 EMSA-PKCS1-v1_5).
_SHA256_DIGESTINFO_PREFIX = bytes.fromhex("3031300d060960864801650304020105000420")

#: Algorithms this module can verify today.
SUPPORTED_ALGORITHMS = ("RS256",)


def _b64url_decode(segment: str) -> bytes:
    pad = "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(segment + pad)


def _int_from_b64url(segment: str) -> int:
    return int.from_bytes(_b64url_decode(segment), "big")


def verify_rs256(signing_input: bytes, signature: bytes, n: int, e: int) -> bool:
    """Verify an RSASSA-PKCS1-v1_5 (RS256) signature against an ``(n, e)`` public key.

    Pure standard-library RSA: recover the padded message ``m = s^e mod n``, then
    rebuild the expected EMSA-PKCS1-v1_5 encoding ``00 01 FF..FF 00 || DigestInfo ||
    SHA-256(signing_input)`` and compare in constant time. Returns False (never raises)
    on any malformed/over-long signature.
    """
    k = (n.bit_length() + 7) // 8
    if not signature or len(signature) > k:
        return False
    s = int.from_bytes(signature, "big")
    if s >= n:
        return False
    m = pow(s, e, n)
    try:
        em = m.to_bytes(k, "big")
    except OverflowError:  # pragma: no cover - guarded by s < n
        return False
    digest = hashlib.sha256(signing_input).digest()
    t = _SHA256_DIGESTINFO_PREFIX + digest
    if k < len(t) + 11:
        return False
    ps = b"\xff" * (k - len(t) - 3)
    expected = b"\x00\x01" + ps + b"\x00" + t
    return hmac.compare_digest(em, expected)


@dataclass
class JWKSClient:
    """A set of JSON Web Keys, indexed by ``kid``, with rotation-aware (re)fetch.

    Construct from a static JWKS dict (tests / pinned keys) via :meth:`from_jwks`, or
    from a URL via :meth:`from_url` (lazily fetched with ``urllib`` and cached for
    ``ttl`` seconds). An unknown ``kid`` triggers at most one refetch (bounded by
    ``min_refetch_interval``) so a freshly rotated key is picked up without hammering
    the endpoint. ``fetcher`` / ``now`` are injectable to keep tests fully offline and
    deterministic.
    """

    url: Optional[str] = None
    ttl: int = 3600
    min_refetch_interval: int = 60
    fetcher: Optional[Callable[[str], Dict[str, Any]]] = None
    now: Callable[[], float] = time.time
    _keys: Dict[str, dict] = field(default_factory=dict)
    _fetched_at: float = 0.0

    # -- construction ------------------------------------------------------- #
    @classmethod
    def from_jwks(cls, jwks: Dict[str, Any]) -> "JWKSClient":
        client = cls(url=None)
        client._index(jwks)
        client._fetched_at = client.now()
        return client

    @classmethod
    def from_url(cls, url: str, *, ttl: int = 3600,
                 fetcher: Optional[Callable[[str], Dict[str, Any]]] = None) -> "JWKSClient":
        return cls(url=url, ttl=ttl, fetcher=fetcher)

    # -- internals ---------------------------------------------------------- #
    def _index(self, jwks: Dict[str, Any]) -> None:
        keys = {}
        for jwk in jwks.get("keys", []) or []:
            kid = jwk.get("kid")
            if kid is not None:
                keys[str(kid)] = jwk
        self._keys = keys

    def _http_fetch(self, url: str) -> Dict[str, Any]:  # pragma: no cover - network
        with urllib.request.urlopen(url, timeout=5) as resp:  # noqa: S310 (configured URL)
            return json.loads(resp.read().decode("utf-8"))

    def _refresh(self) -> None:
        if self.url is None:
            return
        fetch = self.fetcher or self._http_fetch
        self._index(fetch(self.url))
        self._fetched_at = self.now()

    def _stale(self) -> bool:
        if self.url is None:
            return False
        if not self._keys:           # never fetched yet -> fetch on first lookup
            return True
        return (self.now() - self._fetched_at) >= self.ttl

    # -- lookup ------------------------------------------------------------- #
    def get_jwk(self, kid: Optional[str]) -> dict:
        """Return the JWK for ``kid`` (or the sole key when no ``kid`` is given).

        Refetches when the cache is stale, or once (rate-limited) when ``kid`` is
        unknown — the rotation path. Raises ``KeyError`` if it still cannot be found.
        """
        if self._stale():
            self._refresh()
        jwk = self._select(kid)
        if jwk is not None:
            return jwk
        # Unknown kid: a key may have just rotated in. Refetch once, rate-limited.
        if self.url is not None and (self.now() - self._fetched_at) >= self.min_refetch_interval:
            self._refresh()
            jwk = self._select(kid)
            if jwk is not None:
                return jwk
        raise KeyError(f"no JWK for kid={kid!r}")

    def _select(self, kid: Optional[str]) -> Optional[dict]:
        if kid is not None:
            return self._keys.get(str(kid))
        if len(self._keys) == 1:
            return next(iter(self._keys.values()))
        return None


def _check_audience(claim: Any, audience: str) -> bool:
    if isinstance(claim, str):
        return claim == audience
    if isinstance(claim, (list, tuple)):
        return audience in [str(a) for a in claim]
    return False


def decode_and_verify(
    token: str,
    jwks: JWKSClient,
    *,
    issuer: Optional[str] = None,
    audience: Optional[str] = None,
    algorithms: Sequence[str] = SUPPORTED_ALGORITHMS,
    leeway: int = 0,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """Validate an RS256 JWT against a JWKS and return its claims, or raise ``ValueError``.

    Checks, in order: well-formedness, ``alg`` is supported, the signing key resolves
    by ``kid``, the RSA signature verifies, then ``exp`` / ``nbf`` (with ``leeway``),
    ``iss`` (when ``issuer`` given) and ``aud`` (when ``audience`` given). ``now`` is
    injectable for deterministic tests.
    """
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("malformed JWT")
    header_b, payload_b, sig_b = parts
    try:
        header = json.loads(_b64url_decode(header_b))
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"malformed JWT header: {exc}") from exc

    alg = header.get("alg")
    if alg not in algorithms:
        raise UnsupportedAlgorithm(f"unsupported JWT alg {alg!r} (supported: {tuple(algorithms)})")

    try:
        jwk = jwks.get_jwk(header.get("kid"))
    except KeyError as exc:
        raise ValueError(f"no signing key: {exc}") from exc
    if str(jwk.get("kty")) != "RSA":
        raise UnsupportedAlgorithm(f"unsupported JWK kty {jwk.get('kty')!r} for {alg}")

    n = _int_from_b64url(jwk["n"])
    e = _int_from_b64url(jwk["e"])
    signing_input = f"{header_b}.{payload_b}".encode()
    if not verify_rs256(signing_input, _b64url_decode(sig_b), n, e):
        raise ValueError("invalid JWT signature")

    try:
        payload = json.loads(_b64url_decode(payload_b))
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"malformed JWT payload: {exc}") from exc

    clock = int(now if now is not None else time.time())
    exp = payload.get("exp")
    if exp is not None and clock > int(exp) + leeway:
        raise ValueError("JWT expired")
    nbf = payload.get("nbf")
    if nbf is not None and clock + leeway < int(nbf):
        raise ValueError("JWT not yet valid")
    if issuer is not None and payload.get("iss") != issuer:
        raise ValueError(f"JWT issuer mismatch (expected {issuer!r})")
    if audience is not None and not _check_audience(payload.get("aud"), audience):
        raise ValueError(f"JWT audience mismatch (expected {audience!r})")
    return payload


# Module-level cache of URL-backed clients so fetched keys persist across requests.
_URL_CLIENTS: Dict[str, JWKSClient] = {}


def jwks_client_for(
    *,
    static_jwks: Optional[Dict[str, Any]] = None,
    url: Optional[str] = None,
    ttl: int = 3600,
    fetcher: Optional[Callable[[str], Dict[str, Any]]] = None,
) -> Optional[JWKSClient]:
    """Build (and, for URLs, cache) a :class:`JWKSClient` from settings, or None."""
    if static_jwks:
        return JWKSClient.from_jwks(static_jwks)
    if url:
        client = _URL_CLIENTS.get(url)
        if client is None:
            client = JWKSClient.from_url(url, ttl=ttl, fetcher=fetcher)
            _URL_CLIENTS[url] = client
        return client
    return None


def rsa_jwk(n: int, e: int, kid: str, *, alg: str = "RS256") -> Dict[str, Any]:
    """Build a public RSA JWK from raw ``(n, e)`` integers (helper for tooling/tests)."""
    def _b64(i: int) -> str:
        raw = i.to_bytes((i.bit_length() + 7) // 8 or 1, "big")
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
    return {"kty": "RSA", "use": "sig", "alg": alg, "kid": kid,
            "n": _b64(n), "e": _b64(e)}
