"""Offline tests for the Codex provider auth section (Task 4a).

Every network/filesystem seam is monkeypatched: no real network, no real
``~/.codex``. We point ``CODEX_HOME`` at ``tmp_path`` and patch the single
``_http_post_json`` network seam.
"""

from __future__ import annotations

import base64
import json
import stat
import time

import pytest

from riftor.agent import codex_provider


def _make_jwt(claims: dict) -> str:
    def seg(obj: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()

    return f"{seg({'alg': 'none'})}.{seg(claims)}.sig"


def _write_auth(tmp_path, tokens: dict, **extra) -> None:
    data = {"tokens": tokens, **extra}
    (tmp_path / "auth.json").write_text(json.dumps(data))


@pytest.fixture(autouse=True)
def _codex_home(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    return tmp_path


# --- read_tokens -----------------------------------------------------------


def test_read_tokens_returns_pair(tmp_path):
    _write_auth(tmp_path, {"access_token": "at-1", "refresh_token": "rt-1"})
    assert codex_provider.read_tokens() == ("at-1", "rt-1")


def test_read_tokens_missing_file_raises(tmp_path):
    with pytest.raises(RuntimeError) as ei:
        codex_provider.read_tokens()
    assert "codex login" in str(ei.value)


def test_read_tokens_absent_tokens_raises(tmp_path):
    _write_auth(tmp_path, {})  # tokens present but no access/refresh
    with pytest.raises(RuntimeError) as ei:
        codex_provider.read_tokens()
    assert "codex login" in str(ei.value)


# --- account_id ------------------------------------------------------------


def test_account_id_prefers_tokens_field(tmp_path):
    _write_auth(
        tmp_path,
        {"access_token": "at", "refresh_token": "rt", "account_id": "acc-from-field"},
    )
    assert codex_provider.account_id() == "acc-from-field"


def test_account_id_decoded_from_jwt_when_absent(tmp_path):
    at = _make_jwt(
        {
            "https://api.openai.com/auth": {"chatgpt_account_id": "acc-123"},
            "exp": int(time.time()) + 3600,
        }
    )
    _write_auth(tmp_path, {"access_token": at, "refresh_token": "rt"})
    assert codex_provider.account_id() == "acc-123"


def test_account_id_none_when_unresolvable(tmp_path):
    _write_auth(tmp_path, {"access_token": "not-a-jwt", "refresh_token": "rt"})
    assert codex_provider.account_id() is None


# --- should_refresh --------------------------------------------------------


def test_should_refresh_within_window():
    at = _make_jwt({"exp": int(time.time()) + 60})
    assert codex_provider.should_refresh(at) is True


def test_should_refresh_no_exp():
    at = _make_jwt({"foo": "bar"})
    assert codex_provider.should_refresh(at) is True


def test_should_refresh_far_future():
    at = _make_jwt({"exp": int(time.time()) + 3600})
    assert codex_provider.should_refresh(at) is False


# --- refresh_tokens --------------------------------------------------------


def test_refresh_tokens_writes_back(tmp_path, monkeypatch):
    _write_auth(
        tmp_path,
        {"access_token": "old-at", "refresh_token": "old-rt", "account_id": "acc-x"},
        last_refresh="2020-01-01T00:00:00Z",
    )

    captured: dict = {}

    def fake_post(url, body, timeout=30.0):
        captured["url"] = url
        captured["body"] = body
        return {"access_token": "new-at", "refresh_token": "new-rt"}

    monkeypatch.setattr(codex_provider, "_http_post_json", fake_post)

    result = codex_provider.refresh_tokens()
    assert result == "new-at"

    on_disk = json.loads((tmp_path / "auth.json").read_text())
    assert on_disk["tokens"]["access_token"] == "new-at"
    assert on_disk["tokens"]["refresh_token"] == "new-rt"
    # Preserved other keys
    assert on_disk["tokens"]["account_id"] == "acc-x"
    # last_refresh updated to something other than the stale value
    assert on_disk["last_refresh"] != "2020-01-01T00:00:00Z"
    assert on_disk["last_refresh"]

    # File mode is owner-only.
    mode = stat.S_IMODE((tmp_path / "auth.json").stat().st_mode)
    assert mode == 0o600

    # Wire protocol body is exact.
    assert captured["url"] == codex_provider.AUTH_TOKEN_URL
    assert captured["body"] == {
        "client_id": codex_provider.CLIENT_ID,
        "grant_type": "refresh_token",
        "refresh_token": "old-rt",
    }


def test_refresh_tokens_falls_back_to_old_values(tmp_path, monkeypatch):
    _write_auth(
        tmp_path,
        {"access_token": "old-at", "refresh_token": "old-rt", "id_token": "old-id"},
    )

    def fake_post(url, body, timeout=30.0):
        return {"access_token": "new-at"}  # no refresh_token / id_token

    monkeypatch.setattr(codex_provider, "_http_post_json", fake_post)

    assert codex_provider.refresh_tokens() == "new-at"
    on_disk = json.loads((tmp_path / "auth.json").read_text())
    assert on_disk["tokens"]["access_token"] == "new-at"
    assert on_disk["tokens"]["refresh_token"] == "old-rt"  # preserved
    assert on_disk["tokens"]["id_token"] == "old-id"  # preserved


def test_refresh_tokens_missing_access_token_raises(tmp_path, monkeypatch):
    _write_auth(tmp_path, {"access_token": "old-at", "refresh_token": "old-rt"})

    def fake_post(url, body, timeout=30.0):
        return {"refresh_token": "new-rt"}  # no access_token

    monkeypatch.setattr(codex_provider, "_http_post_json", fake_post)

    with pytest.raises(RuntimeError):
        codex_provider.refresh_tokens()
