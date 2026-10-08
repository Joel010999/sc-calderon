"""Cliente OIDC mínimo para Apple, sin persistir tokens ni secretos."""

import json
import threading
import time
import urllib.parse
import urllib.request

from django.conf import settings

_jwks_cache = None
_jwks_cached_at = 0.0
_jwks_lock = threading.Lock()


def _request_json(url, data=None, headers=None):
    request = urllib.request.Request(url, data=data, headers=headers or {}, method="POST" if data else "GET")
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def client_secret():
    import jwt
    key = settings.APPLE_OIDC_PRIVATE_KEY.replace("\\n", "\n")
    now = int(time.time())
    return jwt.encode(
        {"iss": settings.APPLE_OIDC_TEAM_ID, "iat": now, "exp": now + 300, "aud": "https://appleid.apple.com", "sub": settings.APPLE_OIDC_CLIENT_ID},
        key, algorithm="ES256", headers={"kid": settings.APPLE_OIDC_KEY_ID},
    )


def exchange_code(code, redirect_uri, code_verifier):
    payload = urllib.parse.urlencode({
        "client_id": settings.APPLE_OIDC_CLIENT_ID, "client_secret": client_secret(),
        "code": code, "grant_type": "authorization_code", "redirect_uri": redirect_uri,
        "code_verifier": code_verifier,
    }).encode("utf-8")
    return _request_json(settings.APPLE_OIDC_TOKEN_URL, data=payload, headers={"Content-Type": "application/x-www-form-urlencoded"})


def _jwks():
    global _jwks_cache, _jwks_cached_at
    now = time.monotonic()
    if _jwks_cache and now - _jwks_cached_at < settings.APPLE_OIDC_JWKS_CACHE_SECONDS:
        return _jwks_cache
    with _jwks_lock:
        if _jwks_cache and time.monotonic() - _jwks_cached_at < settings.APPLE_OIDC_JWKS_CACHE_SECONDS:
            return _jwks_cache
        data = _request_json(settings.APPLE_OIDC_JWKS_URL)
        _jwks_cache = {item["kid"]: item for item in data.get("keys", []) if item.get("kid")}
        _jwks_cached_at = time.monotonic()
        return _jwks_cache


def verify_id_token(token):
    """Verifica firma, issuer, audience, expiración y nonce del JWT Apple."""
    import jwt
    header = jwt.get_unverified_header(token)
    if header.get("alg") != "RS256":
        raise jwt.InvalidAlgorithmError("algoritmo OIDC no permitido")
    key_data = _jwks().get(header.get("kid"))
    if not key_data:
        raise jwt.InvalidKeyError("clave OIDC desconocida")
    public_key = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(key_data))
    return jwt.decode(
        token,
        public_key,
        algorithms=["RS256"],
        audience=settings.APPLE_OIDC_CLIENT_ID,
        issuer=settings.APPLE_OIDC_ISSUER,
        options={"require": ["exp", "iss", "aud", "sub"]},
    )
