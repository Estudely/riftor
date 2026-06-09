"""Scope target matching — the legal/ethical guardrail. IP/CIDR/domain/wildcard."""

from __future__ import annotations

from riftor.engagement.scope import Target


def test_ip_exact_match():
    t = Target.parse("10.0.0.5")
    assert t.kind == "ip"
    assert t.matches("10.0.0.5")
    assert not t.matches("10.0.0.6")


def test_cidr_match():
    t = Target.parse("10.0.0.0/24")
    assert t.kind == "cidr"
    assert t.matches("10.0.0.7")
    assert not t.matches("10.0.1.7")


def test_domain_matches_self_and_subdomains():
    t = Target.parse("example.com")
    assert t.kind == "domain"
    assert t.matches("example.com")
    assert t.matches("sub.example.com")
    assert t.matches("a.b.example.com")
    assert not t.matches("notexample.com")
    assert not t.matches("example.com.evil.com")


def test_wildcard_matches_subdomains_only_not_base():
    """``*.example.com`` is a subdomain wildcard: it must match subdomains but
    NOT the bare base domain. Otherwise scope is broader than the operator asked
    for, on the guardrail that controls what hosts the agent may touch."""
    t = Target.parse("*.example.com")
    assert t.kind == "wildcard"
    assert t.matches("sub.example.com")
    assert t.matches("a.b.example.com")
    assert not t.matches("example.com")  # the bug: base domain must not match
    assert not t.matches("notexample.com")
