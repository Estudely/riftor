"""Toolchain doctor: detection, summary, and rendering (deterministic via mock)."""

from __future__ import annotations

from riftor.engagement import doctor


def test_check_toolchain_all_present(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda name: f"/usr/bin/{name}")
    statuses = doctor.check_toolchain()
    assert statuses and all(s.present for s in statuses)
    # one entry per declared tool
    declared = sum(len(v) for v in doctor.TOOLCHAIN.values())
    assert len(statuses) == declared


def test_check_toolchain_all_missing(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda name: None)
    statuses = doctor.check_toolchain()
    assert statuses and not any(s.present for s in statuses)


def test_summarize(monkeypatch):
    # only nmap present
    monkeypatch.setattr(doctor.shutil, "which", lambda name: "/usr/bin/nmap" if name == "nmap" else None)
    s = doctor.summarize(doctor.check_toolchain())
    assert s["present"] == 1
    assert s["missing"] == s["total"] - 1
    assert "nuclei" in s["missing_names"] and "nmap" not in s["missing_names"]


def test_render_markdown_flags_missing(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda name: None)
    md = doctor.render_markdown(doctor.check_toolchain())
    assert "not on PATH" in md
    assert "aren't fatal" in md
    assert "Recon" in md and "Intrusion" in md


def test_render_markdown_full(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda name: f"/usr/bin/{name}")
    md = doctor.render_markdown(doctor.check_toolchain())
    assert "Full toolchain available" in md


def test_render_plain(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda name: "/usr/bin/nmap" if name == "nmap" else None)
    plain = doctor.render_plain(doctor.check_toolchain())
    assert "MISSING" in plain and "ok" in plain
    assert "missing (not fatal):" in plain
