"""Parse bug-bounty program scope into riftor in/out targets.

Supports HackerOne structured-scopes JSON (API response or saved file) and a
generic line/URL list. Network calls are opt-in via ``fetch_hackerone`` — parsers
themselves are pure and offline-testable.
"""

from __future__ import annotations

import base64
import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

# Asset types that map cleanly onto riftor's host/CIDR/wildcard Target model.
_HOSTISH = {
    "url",
    "wildcard",
    "domain",
    "cidr",
    "ip_address",
    "ip-address",
    "other",  # sometimes used for host-shaped "other"
}


@dataclass
class ParsedBountyScope:
    """Normalized scope extracted from a platform export."""

    in_scope: list[str] = field(default_factory=list)
    out_of_scope: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)  # non-host assets (apps, etc.)
    source: str = ""


def _normalize_identifier(raw: str) -> str | None:
    """Turn a platform asset identifier into something Target.parse can use."""
    value = (raw or "").strip()
    if not value:
        return None
    # Strip scheme / path for URL assets: https://api.example.com/v1 → api.example.com
    if "://" in value or value.startswith("//"):
        parsed = urlparse(value if "://" in value else f"https:{value}")
        host = (parsed.hostname or "").lower()
        if not host:
            return None
        # Preserve wildcard hosts that arrived as https://*.example.com
        if host.startswith("*."):
            return host
        return host
    # Bare wildcard / domain / CIDR / IP — keep as-is (lowercased for domains).
    if value.startswith("*.") or "/" in value or re.fullmatch(r"[\d.]+", value):
        return value.lower() if not value.startswith("*.") else value.lower()
    return value.lower().rstrip(".")


def _eligible_in(attrs: dict[str, Any]) -> bool:
    """HackerOne: prefer eligible_for_submission; fall back to eligible_for_bounty."""
    if "eligible_for_submission" in attrs:
        return bool(attrs.get("eligible_for_submission"))
    if "eligible_for_bounty" in attrs:
        return bool(attrs.get("eligible_for_bounty"))
    return True


def parse_hackerone(payload: str | dict | list) -> ParsedBountyScope:
    """Parse a HackerOne structured_scopes JSON document.

    Accepts the full API envelope ``{"data": [...]}``, a bare list of scope
    objects, or a JSON string of either.
    """
    data: Any = payload
    if isinstance(payload, str):
        data = json.loads(payload)
    items: list[Any]
    if isinstance(data, dict):
        items = list(data.get("data") or [])
    elif isinstance(data, list):
        items = data
    else:
        raise ValueError("hackerone scope JSON must be an object or array")

    result = ParsedBountyScope(source="hackerone")
    seen_in: set[str] = set()
    seen_out: set[str] = set()

    for item in items:
        if not isinstance(item, dict):
            continue
        attrs = item.get("attributes") if isinstance(item.get("attributes"), dict) else item
        if not isinstance(attrs, dict):
            continue
        asset_type = str(attrs.get("asset_type") or "").strip().lower()
        identifier = str(attrs.get("asset_identifier") or "").strip()
        if not identifier:
            continue
        if asset_type and asset_type not in _HOSTISH:
            # Mobile apps, source code, hardware, etc. — surface for the operator.
            result.skipped.append(f"{asset_type}:{identifier}")
            continue
        normalized = _normalize_identifier(identifier)
        if not normalized:
            result.skipped.append(identifier)
            continue
        if _eligible_in(attrs):
            if normalized not in seen_in:
                result.in_scope.append(normalized)
                seen_in.add(normalized)
        else:
            if normalized not in seen_out:
                result.out_of_scope.append(normalized)
                seen_out.add(normalized)
    return result


def parse_generic(text: str) -> ParsedBountyScope:
    """Parse a plain target list (one per line; ``out:`` / ``#`` comments).

    Same line grammar as ``Engagement.import_scope``, returned as ParsedBountyScope
    so callers share one apply path.
    """
    result = ParsedBountyScope(source="generic")
    for raw in (text or "").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        mode = "in"
        if line.lower().startswith("out:"):
            mode, line = "out", line[4:].strip()
        elif line.lower().startswith("in:"):
            line = line[3:].strip()
        if not line:
            continue
        normalized = _normalize_identifier(line) or line
        if mode == "in":
            result.in_scope.append(normalized)
        else:
            result.out_of_scope.append(normalized)
    return result


def parse_bounty_file(text: str, *, platform: str = "auto") -> ParsedBountyScope:
    """Auto-detect HackerOne JSON vs generic line list."""
    platform = (platform or "auto").lower()
    stripped = (text or "").lstrip()
    if platform in ("hackerone", "h1") or (
        platform == "auto" and stripped[:1] in ("{", "[")
    ):
        try:
            return parse_hackerone(text)
        except (json.JSONDecodeError, ValueError):
            if platform != "auto":
                raise
    return parse_generic(text)


def fetch_hackerone(
    handle: str,
    *,
    username: str | None = None,
    token: str | None = None,
    timeout: float = 30.0,
) -> ParsedBountyScope:
    """GET ``/v1/hackers/programs/{handle}/structured_scopes`` and parse it.

    Credentials: explicit args, else ``HACKERONE_USERNAME`` + ``HACKERONE_TOKEN``
    (or ``HACKERONE_API_TOKEN``) env vars.
    """
    handle = (handle or "").strip().lstrip("@")
    if not handle:
        raise ValueError("program handle is required")
    user = username or os.environ.get("HACKERONE_USERNAME") or ""
    key = (
        token
        or os.environ.get("HACKERONE_TOKEN")
        or os.environ.get("HACKERONE_API_TOKEN")
        or ""
    )
    if not user or not key:
        raise ValueError(
            "HackerOne credentials missing — set HACKERONE_USERNAME and "
            "HACKERONE_TOKEN (or pass username=/token=)"
        )
    url = (
        f"https://api.hackerone.com/v1/hackers/programs/{handle}/structured_scopes"
        "?page[size]=100"
    )
    auth = base64.b64encode(f"{user}:{key}".encode()).decode()
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Basic {auth}",
            "Accept": "application/json",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — fixed https API
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:200]
        raise RuntimeError(f"HackerOne API {exc.code}: {detail or exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"HackerOne API unreachable: {exc.reason}") from exc
    parsed = parse_hackerone(body)
    parsed.source = f"hackerone:{handle}"
    return parsed


def apply_bounty_scope(engagement, parsed: ParsedBountyScope) -> tuple[int, int]:
    """Add parsed targets to the engagement. Returns (in_added, out_added)."""
    added_in = added_out = 0
    for target in parsed.in_scope:
        engagement.add_scope(target, "in")
        added_in += 1
    for target in parsed.out_of_scope:
        engagement.add_scope(target, "out")
        added_out += 1
    return added_in, added_out
