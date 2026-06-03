"""Scan parsers: structured extraction plus the new skip/error diagnostics."""

from __future__ import annotations

from riftor.engagement.parsers import parse


def test_nmap_services():
    s = parse("nmap", (
        "Nmap scan report for h (10.0.0.5)\n"
        "PORT STATE SERVICE VERSION\n"
        "22/tcp open ssh OpenSSH 8.2\n443/tcp closed https\n"
    ))
    assert len(s.services) == 1 and s.services[0]["port"] == 22


def test_httpx_skip_count():
    s = parse("httpx", "https://example.com [200]\nnot-a-url line here\n")
    assert len(s.services) == 1
    assert s.skipped == 1


def test_nuclei_json_error_count():
    s = parse("nuclei", '{"broken json\n[tpl] [http] [high] https://x\n')
    assert s.json_errors == 1
    assert len(s.findings) == 1  # the valid bracket line still parses


def test_nuclei_severity():
    s = parse("nuclei", "[CVE-x] [http] [critical] https://x/y\n")
    assert s.findings[0]["severity"] == "critical"
