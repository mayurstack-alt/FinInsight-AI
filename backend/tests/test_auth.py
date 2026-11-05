import asyncio
from uuid import uuid4
import jwt
import pytest
import httpx

from app.modules.auth.service import (
    AuthenticatedUser,
    AuthProviderUnavailable,
    InvalidAccessToken,
    SupabaseTokenVerifier,
)


@pytest.mark.asyncio
async def test_empty_token_raises_invalid() -> None:
    verifier = SupabaseTokenVerifier("https://hwfspvefbxcxowzefkbw.supabase.co")
    with pytest.raises(InvalidAccessToken):
        await verifier.verify("")


@pytest.mark.asyncio
async def test_oversized_token_raises_invalid() -> None:
    verifier = SupabaseTokenVerifier("https://hwfspvefbxcxowzefkbw.supabase.co")
    with pytest.raises(InvalidAccessToken):
        await verifier.verify("a" * 8193)


@pytest.mark.asyncio
async def test_malformed_token_raises_invalid() -> None:
    verifier = SupabaseTokenVerifier("https://hwfspvefbxcxowzefkbw.supabase.co")
    with pytest.raises(InvalidAccessToken):
        await verifier.verify("not-a-valid-jwt")


@pytest.mark.asyncio
async def test_unsupported_algorithm_raises_invalid() -> None:
    # "none" algorithm token
    token = jwt.encode({"sub": str(uuid4())}, key="", algorithm="none")
    verifier = SupabaseTokenVerifier("https://hwfspvefbxcxowzefkbw.supabase.co")
    with pytest.raises(InvalidAccessToken):
        await verifier.verify(token)


@pytest.mark.asyncio
async def test_hs256_without_publishable_key_raises_unavailable() -> None:
    token = jwt.encode({"sub": str(uuid4())}, key="secret-key-that-is-at-least-32-bytes-long!", algorithm="HS256")
    verifier = SupabaseTokenVerifier("https://hwfspvefbxcxowzefkbw.supabase.co", publishable_key=None)
    with pytest.raises(AuthProviderUnavailable):
        await verifier.verify(token)


@pytest.mark.asyncio
async def test_hs256_with_mock_auth_server_success(monkeypatch) -> None:
    user_id = str(uuid4())
    email = "trader@example.com"
    token = jwt.encode({"sub": user_id}, key="secret-key-that-is-at-least-32-bytes-long!", algorithm="HS256")

    class FakeResponse:
        status_code = 200
        is_error = False

        def json(self):
            return {"id": user_id, "email": email}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

        async def get(self, url, headers):
            assert headers["apikey"] == "test-pub-key"
            assert headers["Authorization"] == f"Bearer {token}"
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

    verifier = SupabaseTokenVerifier(
        "https://hwfspvefbxcxowzefkbw.supabase.co",
        publishable_key="test-pub-key",
    )
    user = await verifier.verify(token)
    assert user.id == user_id
    assert user.email == email
