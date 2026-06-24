"""Offline unit tests for asymmetric (RS256 / JWKS) bearer-token validation (gap.md C1).

Everything runs from a FIXED, test-only RSA keypair embedded below and signs tokens
in-process with pure-Python RSA, so there is no network and no third-party crypto
dependency. The same modular-exponentiation path the production verifier uses is
exercised end-to-end (sign with d, verify with e), plus issuer/audience/expiry checks,
key rotation, algorithm rejection, and the auth fallback to the HS256 dev path.

Run from ``ReClass Model/``:

    ../.venv/bin/python -m unittest tests.test_oidc -v
"""

import base64
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api import auth  # noqa: E402
from api.oidc import (  # noqa: E402
    JWKSClient,
    UnsupportedAlgorithm,
    decode_and_verify,
    jwks_client_for,
    rsa_jwk,
    verify_rs256,
)
from api.settings import Settings  # noqa: E402

# Fixed, test-only 1024-bit RSA keypair (generated once; NOT for production use).
_N = int(
    "1017088163940306197514799455309950078524596060671996837246147710740603125089610340636742"
    "9778985594812844846481549637181170104102226826699329563614806533607483243648291443068667"
    "4631308758963801734858159943660888506508131570142947397193589943082989346761123777935558"
    "009620616588018259998804373794713327970770659"
)
_E = 65537
_D = int(
    "4507579797922729680457962704957123461670032589562864967547242055397839047900839578222103"
    "5129871156955472262394770772529576525268043728196595385078971537996623162357653856576840"
    "8864273605861478409740331779217254683671768317072568189116294797628963265414067722979448"
    "48908757512956862241040886328963578113931273"
)
_KID = "test-key-1"


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _sign_rs256(payload: dict, *, kid: str = _KID, n: int = _N, d: int = _D,
                alg: str = "RS256") -> str:
    """Mint an RS256 JWT signed with the test private key (pure-Python RSA)."""
    header = {"alg": alg, "typ": "JWT", "kid": kid}
    header_b = _b64url(json.dumps(header).encode())
    payload_b = _b64url(json.dumps(payload).encode())
    signing_input = f"{header_b}.{payload_b}".encode()
    import hashlib
    digest = hashlib.sha256(signing_input).digest()
    prefix = bytes.fromhex("3031300d060960864801650304020105000420")
    k = (n.bit_length() + 7) // 8
    t = prefix + digest
    ps = b"\xff" * (k - len(t) - 3)
    em = b"\x00\x01" + ps + b"\x00" + t
    sig_int = pow(int.from_bytes(em, "big"), d, n)
    sig = sig_int.to_bytes(k, "big")
    return f"{header_b}.{payload_b}.{_b64url(sig)}"


def _jwks(kid: str = _KID) -> dict:
    return {"keys": [rsa_jwk(_N, _E, kid)]}


def _claims(**over) -> dict:
    base = {"sub": "u-1", "tenant_id": "t-1", "roles": ["reviewer"],
            "iss": "https://idp.example", "aud": "reclass-api",
            "iat": 1000, "exp": 1000 + 3600}
    base.update(over)
    return base


class TestVerifyRs256(unittest.TestCase):
    def test_valid_signature_verifies(self):
        token = _sign_rs256(_claims())
        h, p, s = token.split(".")
        signing_input = f"{h}.{p}".encode()
        sig = base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))
        self.assertTrue(verify_rs256(signing_input, sig, _N, _E))

    def test_tampered_input_fails(self):
        token = _sign_rs256(_claims())
        h, p, s = token.split(".")
        sig = base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))
        self.assertFalse(verify_rs256(b"not-the-signed-input", sig, _N, _E))

    def test_garbage_signature_returns_false_not_raises(self):
        self.assertFalse(verify_rs256(b"x", b"\x00\x01\x02", _N, _E))


class TestDecodeAndVerify(unittest.TestCase):
    def setUp(self):
        self.jwks = JWKSClient.from_jwks(_jwks())

    def test_valid_token_returns_claims(self):
        token = _sign_rs256(_claims())
        payload = decode_and_verify(token, self.jwks, issuer="https://idp.example",
                                    audience="reclass-api", now=2000)
        self.assertEqual(payload["sub"], "u-1")
        self.assertEqual(payload["tenant_id"], "t-1")

    def test_tampered_payload_rejected(self):
        token = _sign_rs256(_claims())
        h, p, s = token.split(".")
        forged_p = _b64url(json.dumps(_claims(roles=["admin"])).encode())
        forged = f"{h}.{forged_p}.{s}"
        with self.assertRaises(ValueError):
            decode_and_verify(forged, self.jwks, now=2000)

    def test_expired_token_rejected_and_leeway_allows(self):
        token = _sign_rs256(_claims(exp=1500))
        with self.assertRaises(ValueError):
            decode_and_verify(token, self.jwks, now=2000)
        # within leeway -> accepted
        self.assertEqual(
            decode_and_verify(token, self.jwks, now=2000, leeway=600)["sub"], "u-1")

    def test_not_yet_valid_rejected(self):
        token = _sign_rs256(_claims(nbf=5000))
        with self.assertRaises(ValueError):
            decode_and_verify(token, self.jwks, now=2000)

    def test_issuer_mismatch_rejected(self):
        token = _sign_rs256(_claims(iss="https://evil.example"))
        with self.assertRaises(ValueError):
            decode_and_verify(token, self.jwks, issuer="https://idp.example", now=2000)

    def test_audience_mismatch_rejected(self):
        token = _sign_rs256(_claims(aud="some-other-api"))
        with self.assertRaises(ValueError):
            decode_and_verify(token, self.jwks, audience="reclass-api", now=2000)

    def test_audience_list_accepted(self):
        token = _sign_rs256(_claims(aud=["x", "reclass-api"]))
        self.assertEqual(
            decode_and_verify(token, self.jwks, audience="reclass-api", now=2000)["sub"], "u-1")

    def test_hs256_algorithm_rejected(self):
        token = _sign_rs256(_claims(), alg="HS256")
        with self.assertRaises(UnsupportedAlgorithm):
            decode_and_verify(token, self.jwks, now=2000)

    def test_unknown_kid_is_value_error(self):
        token = _sign_rs256(_claims(), kid="rotated-away")
        with self.assertRaises(ValueError):
            decode_and_verify(token, self.jwks, now=2000)


class TestJWKSRotation(unittest.TestCase):
    def test_url_client_fetches_then_picks_up_rotated_key(self):
        clock = {"t": 1000.0}
        state = {"jwks": _jwks("k1")}

        def fetcher(url):
            return state["jwks"]

        client = JWKSClient.from_url("https://idp/jwks", ttl=3600, fetcher=fetcher)
        client.now = lambda: clock["t"]

        # First lookup triggers the initial fetch and resolves k1.
        self.assertEqual(client.get_jwk("k1")["kid"], "k1")

        # IdP rotates: k2 is now published. Advancing past the refetch interval and
        # asking for the unknown kid triggers a rotation-aware refetch.
        state["jwks"] = _jwks("k2")
        clock["t"] = 1000.0 + 120  # past min_refetch_interval (60)
        self.assertEqual(client.get_jwk("k2")["kid"], "k2")

    def test_unknown_kid_refetch_rate_limited(self):
        clock = {"t": 1000.0}
        calls = {"n": 0}

        def fetcher(url):
            calls["n"] += 1
            return _jwks("k1")

        client = JWKSClient.from_url("https://idp/jwks", fetcher=fetcher)
        client.now = lambda: clock["t"]
        client.get_jwk("k1")          # initial fetch (calls=1)
        with self.assertRaises(KeyError):
            client.get_jwk("missing")  # within interval -> no refetch
        self.assertEqual(calls["n"], 1)


class TestAuthIntegration(unittest.TestCase):
    def _settings(self, **over) -> Settings:
        base = dict(environment="production", oidc_issuer="https://idp.example",
                    oidc_audience="reclass-api", oidc_jwks=_jwks())
        base.update(over)
        return Settings(**base)

    def test_authenticate_bearer_accepts_rs256(self):
        token = _sign_rs256(_claims(exp=9999999999))
        user = auth.authenticate_bearer(token, self._settings())
        self.assertEqual(user.tenant_id, "t-1")
        self.assertTrue(user.has_role("reviewer"))

    def test_oidc_enabled_flag(self):
        self.assertTrue(auth.oidc_enabled(self._settings()))
        self.assertFalse(auth.oidc_enabled(Settings()))

    def test_falls_back_to_hs256_when_not_an_oidc_token(self):
        # OIDC is configured, but an HS256 dev token must still authenticate via the
        # symmetric path (dev path intact behind RECLASS_API_ENV).
        settings = self._settings(jwt_secret="devsecret")
        hs = auth.issue_jwt(user_id="dev", tenant_id="t-9", roles=["reviewer"],
                            secret="devsecret")
        user = auth.authenticate_bearer(hs, settings)
        self.assertEqual(user.tenant_id, "t-9")

    def test_oidc_auth_mode_disables_hs256_fallback(self):
        from fastapi import HTTPException

        settings = self._settings(jwt_secret="devsecret", auth_mode="oidc")
        hs = auth.issue_jwt(
            user_id="dev",
            tenant_id="t-9",
            roles=["reviewer"],
            secret="devsecret",
        )
        with self.assertRaises(HTTPException):
            auth.authenticate_bearer(hs, settings)

    def test_invalid_token_rejected_with_401(self):
        from fastapi import HTTPException
        with self.assertRaises(HTTPException):
            auth.authenticate_bearer("not.a.jwt", self._settings())

    def test_jwks_client_for_builds_from_static(self):
        self.assertIsInstance(jwks_client_for(static_jwks=_jwks()), JWKSClient)
        self.assertIsNone(jwks_client_for())


if __name__ == "__main__":
    unittest.main()
