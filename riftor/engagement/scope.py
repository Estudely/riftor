"""Scope: in-scope / out-of-scope targets, matching, and host extraction.

This is the legal/ethical guardrail. The agent loop extracts hosts from
scope-sensitive tool calls (bash, webfetch) and refuses to touch anything that
isn't in scope unless the operator explicitly overrides for that one call.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass

_IPV4 = re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b")
_URL_HOST = re.compile(r"https?://([^/:\s\"'<>]+)", re.IGNORECASE)
_DOMAIN = re.compile(r"\b(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,24}\b")

# TLDs we treat as real domains during extraction, so local file names like
# ``config.py`` or ``notes.md`` aren't mistaken for targets. Not exhaustive —
# the operator can extend scope, and the per-call override covers misses.
_TLDS = {
    "com", "net", "org", "io", "ai", "co", "dev", "app", "xyz", "me", "info",
    "biz", "gov", "edu", "mil", "uk", "us", "de", "fr", "nl", "eu", "in", "ca",
    "au", "jp", "cn", "ru", "br", "online", "site", "tech", "cloud", "store",
    "local", "internal", "corp", "lan", "htb",
}


def _is_ip(token: str) -> bool:
    try:
        ipaddress.ip_address(token)
        return True
    except ValueError:
        return False


def extract_hosts(text: str) -> set[str]:
    """Best-effort extraction of host-like tokens (IPs, URL hosts, domains)."""
    hosts: set[str] = set()
    text = text or ""
    for host in _URL_HOST.findall(text):
        hosts.add(host.split("@")[-1].split(":")[0].lower())
    for ip in _IPV4.findall(text):
        hosts.add(ip.lower())
    for dom in _DOMAIN.findall(text):
        token = dom.lower().rstrip(".")
        if _is_ip(token):
            continue
        if token.rsplit(".", 1)[-1] in _TLDS:
            hosts.add(token)
    return hosts


@dataclass(frozen=True)
class Target:
    raw: str
    kind: str  # ip | cidr | domain | wildcard

    @staticmethod
    def parse(raw: str) -> "Target":
        value = raw.strip().lower()
        if "://" in value:
            value = value.split("://", 1)[1]
        value = value.strip("/")
        if re.fullmatch(r"[0-9.]+/\d{1,2}", value):
            try:
                ipaddress.ip_network(value, strict=False)
                return Target(value, "cidr")
            except ValueError:
                pass
        host = value.split("/")[0].split(":")[0]
        if host.startswith("*."):
            return Target(host, "wildcard")
        if _is_ip(host):
            return Target(host, "ip")
        return Target(host, "domain")

    def matches(self, host: str) -> bool:
        host = host.lower().split(":")[0]
        if self.kind == "ip":
            return host == self.raw
        if self.kind == "cidr":
            try:
                return ipaddress.ip_address(host) in ipaddress.ip_network(self.raw, strict=False)
            except ValueError:
                return False
        if self.kind == "wildcard":
            base = self.raw[2:]
            return host == base or host.endswith("." + base)
        # domain: the host itself and any subdomain
        return host == self.raw or host.endswith("." + self.raw)


class Scope:
    def __init__(self) -> None:
        self._in: list[Target] = []
        self._out: list[Target] = []

    def add(self, raw: str, mode: str = "in") -> Target:
        target = Target.parse(raw)
        bucket = self._in if mode == "in" else self._out
        if target not in bucket:
            bucket.append(target)
        return target

    def remove(self, raw: str) -> bool:
        target = Target.parse(raw)
        removed = False
        for bucket in (self._in, self._out):
            if target in bucket:
                bucket.remove(target)
                removed = True
        return removed

    def clear(self) -> None:
        self._in.clear()
        self._out.clear()

    @property
    def in_scope(self) -> list[Target]:
        return list(self._in)

    @property
    def out_of_scope(self) -> list[Target]:
        return list(self._out)

    def is_active(self) -> bool:
        return bool(self._in or self._out)

    def is_in_scope(self, host: str) -> bool:
        host = host.lower().split(":")[0]
        if any(t.matches(host) for t in self._out):
            return False
        if not self._in:
            return True
        return any(t.matches(host) for t in self._in)

    def violations(self, text: str) -> list[str]:
        """Hosts referenced in ``text`` that are not allowed by the scope."""
        if not self.is_active():
            return []
        return sorted(h for h in extract_hosts(text) if not self.is_in_scope(h))
