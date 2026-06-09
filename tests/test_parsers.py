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


def test_nmap_rejects_out_of_range_port_normal():
    """A port outside 0..65535 isn't a valid service — drop it (counted as
    skipped), don't persist garbage to the engagement DB."""
    s = parse("nmap", (
        "Nmap scan report for h (10.0.0.5)\n"
        "PORT STATE SERVICE VERSION\n"
        "22/tcp open ssh OpenSSH 8.2\n"
        "65536/tcp open weird Bogus 1.0\n"
    ))
    ports = [svc["port"] for svc in s.services]
    assert ports == [22]
    assert s.skipped == 1


def test_nmap_rejects_out_of_range_port_greppable():
    s = parse("nmap", (
        "Host: 10.0.0.5 ()  Ports: 22/open/tcp//ssh//OpenSSH/, "
        "70000/open/tcp//weird//Bogus/\n"
    ))
    ports = [svc["port"] for svc in s.services]
    assert ports == [22]
    assert s.skipped == 1


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
