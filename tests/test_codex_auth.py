"""Codex auth-status helper: reads ~/.codex/auth.json, never raises."""

from __future__ import annotations

import base64
import json
import time

import riftor.codex_auth as ca


def _make_jwt(exp: int) -> str:
    """A minimal unsigned JWT with the given exp claim (header.payload.sig)."""
    def seg(obj: dict) -> str:
        raw = json.dumps(obj).encode("utf-8")
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    return f"{seg({'alg': 'none'})}.{seg({'exp': exp})}.sig"


def _write_auth(tmp_path, access_token: str) -> None:
    (tmp_path / "auth.json").write_text(
        json.dumps({"tokens": {"access_token": access_token}})
    )


def test_missing_file_is_not_logged_in(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    status = ca.auth_status()
    assert status.logged_in is False
    assert "codex login" in status.detail


def test_valid_future_token_is_logged_in(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    _write_auth(tmp_path, _make_jwt(int(time.time()) + 3600))
    status = ca.auth_status()
    assert status.logged_in is True
    assert status.expires_in_s is not None
    assert 0 < status.expires_in_s <= 3600


def test_garbage_token_degrades_without_raising(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    _write_auth(tmp_path, "not-a-jwt")
    status = ca.auth_status()           # must NOT raise
    assert status.logged_in is True     # token present, just unparseable
    assert status.expires_in_s is None


def test_malformed_json_degrades_without_raising(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    (tmp_path / "auth.json").write_text("{ this is not json")
    status = ca.auth_status()           # must NOT raise
    assert status.logged_in is False


def test_codex_home_defaults_to_home(monkeypatch):
    monkeypatch.delenv("CODEX_HOME", raising=False)
    from pathlib import Path
    assert ca.codex_home() == Path.home() / ".codex"


def test_expired_token_is_not_logged_in(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    _write_auth(tmp_path, _make_jwt(int(time.time()) - 1))
    status = ca.auth_status()
    assert status.logged_in is False
    assert status.expires_in_s == 0
    assert "expired" in status.detail


def test_doctor_plain_includes_codex_line(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))  # no auth.json => not logged in
    from riftor.engagement.doctor import check_toolchain, render_plain
    out = render_plain(check_toolchain())
    assert "Codex" in out
    assert "codex login" in out
