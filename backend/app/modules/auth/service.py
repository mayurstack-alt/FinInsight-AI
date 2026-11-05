"""Verify Supabase access tokens and expose only verified user identity."""

import asyncio
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, ClassVar
from uuid import UUID

import httpx
import jwt


class InvalidAccessToken(Exception):
    """The presented bearer token is invalid, expired, or has invalid claims."""


class AuthProviderUnavailable(Exception):
    """Supabase Auth/JWKS could not be reached to validate a token."""


@dataclass(frozen=True)
class AuthenticatedUser:
    id: str
    email: str | None


class SupabaseTokenVerifier:
    """Verify asymmetric JWTs locally and legacy HS256 JWTs at Supabase Auth."""

    _ASYMMETRIC_ALGORITHMS: ClassVar[set[str]] = {"ES256", "RS256", "EdDSA"}

    def __init__(self, project_url: str, publishable_key: str | None = None) -> None:
        self.issuer = f"{project_url.rstrip('/')}/auth/v1"
        self.jwks_url = f"{self.issuer}/.well-known/jwks.json"
        self.user_url = f"{self.issuer}/user"
        self.publishable_key = publishable_key
        self._keys: dict[str, dict[str, Any]] = {}
        self._keys_expire_at = 0.0
        self._missing_kid_refresh_at = 0.0
        self._refresh_lock = asyncio.Lock()

    async def verify(self, token: str) -> AuthenticatedUser:
        if not token or len(token) > 8192:
            raise InvalidAccessToken
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise InvalidAccessToken from exc

        algorithm = header.get("alg")
        if algorithm == "HS256":
            return await self._verify_legacy_token_with_auth_server(token)
        if algorithm not in self._ASYMMETRIC_ALGORITHMS:
            raise InvalidAccessToken

        key_id = header.get("kid")
        if not isinstance(key_id, str) or not key_id:
            raise InvalidAccessToken
        jwk_data = await self._get_jwk(key_id)
        if jwk_data.get("alg") != algorithm:
            raise InvalidAccessToken

        try:
            signing_key = jwt.PyJWK.from_dict(jwk_data).key
            claims = jwt.decode(
                token,
                key=signing_key,
                algorithms=[algorithm],
                audience="authenticated",
                issuer=self.issuer,
                options={"require": ["exp", "sub", "iss", "aud"]},
            )
        except (jwt.PyJWTError, TypeError, ValueError) as exc:
            raise InvalidAccessToken from exc

        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject:
            raise InvalidAccessToken
        email = claims.get("email")
        return self._user_identity(subject, email)

    async def _get_jwk(self, key_id: str) -> dict[str, Any]:
        async with self._refresh_lock:
            now = time.monotonic()
            if now >= self._keys_expire_at:
                await self._refresh_jwks()
            elif key_id not in self._keys and now >= self._missing_kid_refresh_at:
                self._missing_kid_refresh_at = now + 30
                await self._refresh_jwks()
            jwk_data = self._keys.get(key_id)
            if jwk_data is None:
                raise InvalidAccessToken
            return jwk_data

    async def _refresh_jwks(self) -> None:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(self.jwks_url)
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise AuthProviderUnavailable from exc

        keys = payload.get("keys") if isinstance(payload, dict) else None
        if not isinstance(keys, list):
            raise AuthProviderUnavailable
        self._keys = {
            key["kid"]: key
            for key in keys
            if isinstance(key, dict)
            and isinstance(key.get("kid"), str)
            and key.get("use", "sig") == "sig"
        }
        self._keys_expire_at = time.monotonic() + 600
        self._missing_kid_refresh_at = time.monotonic() + 30

    async def _verify_legacy_token_with_auth_server(self, token: str) -> AuthenticatedUser:
        if not self.publishable_key:
            raise AuthProviderUnavailable
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(
                    self.user_url,
                    headers={
                        "apikey": self.publishable_key,
                        "Authorization": f"Bearer {token}",
                    },
                )
        except httpx.HTTPError as exc:
            raise AuthProviderUnavailable from exc

        if response.status_code in (401, 403):
            raise InvalidAccessToken
        if response.is_error:
            raise AuthProviderUnavailable
        try:
            user = response.json()
        except ValueError as exc:
            raise AuthProviderUnavailable from exc
        user_id = user.get("id") if isinstance(user, dict) else None
        email = user.get("email") if isinstance(user, dict) else None
        if not isinstance(user_id, str) or not user_id:
            raise AuthProviderUnavailable
        try:
            return self._user_identity(user_id, email)
        except InvalidAccessToken as exc:
            raise AuthProviderUnavailable from exc

    @staticmethod
    def _user_identity(user_id: str, email: Any) -> AuthenticatedUser:
        try:
            canonical_user_id = str(UUID(user_id))
        except ValueError as exc:
            raise InvalidAccessToken from exc
        return AuthenticatedUser(
            id=canonical_user_id,
            email=email if isinstance(email, str) else None,
        )


@lru_cache(maxsize=4)
def get_token_verifier(project_url: str, publishable_key: str | None) -> SupabaseTokenVerifier:
    return SupabaseTokenVerifier(project_url, publishable_key)
