"""Parse raw recon-tool output into structured services and findings.

Supports the common output shapes the agent will actually capture from `bash`:
- **nmap**: normal `-sV` stdout and greppable `-oG` output
- **httpx**: default bracketed lines and `-json` JSONL
- **nuclei**: default bracketed lines and `-jsonl` JSONL

Each parser returns a :class:`ParsedScan` (services + findings) that the
``import_scan`` tool records into the engagement.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_SEVERITIES = {"info", "low", "medium", "high", "critical"}


@dataclass
class ParsedScan:
    services: list[dict] = field(default_factory=list)
    findings: list[dict] = field(default_factory=list)


def _sev(value: str) -> str:
    value = (value or "").strip().lower()
    return value if value in _SEVERITIES else "info"


def _host_port(url: str) -> tuple[str, int | None]:
    if "://" not in url:
        url = "//" + url
    parts = urlsplit(url)
    host = parts.hostname or ""
    port = parts.port
    if port is None:
        port = 443 if parts.scheme == "https" else 80 if parts.scheme == "http" else None
    return host, port


def _scheme_service(url: str) -> str:
    return "https" if url.lower().startswith("https") else "http"


def parse_nmap(text: str) -> ParsedScan:
    scan = ParsedScan()
    host: str | None = None
    for raw in text.splitlines():
        line = raw.rstrip()

        report = re.match(r"Nmap scan report for (.+)", line)
        if report:
            target = report.group(1).strip()
            paren = re.match(r"(.+) \(([\d.]+)\)$", target)
            host = paren.group(2) if paren else target
            continue

        # greppable: "Host: 10.0.0.5 ()  Ports: 22/open/tcp//ssh//OpenSSH/, ..."
        grep = re.match(r"Host:\s+(\S+).*?Ports:\s+(.*)", line)
        if grep:
            ghost = grep.group(1)
            for chunk in grep.group(2).split(","):
                fields = chunk.strip().split("/")
                if len(fields) >= 7 and fields[1] == "open":
                    scan.services.append(
                        {
                            "host": ghost,
                            "port": int(fields[0]),
                            "proto": fields[2],
                            "service": fields[4],
                            "version": fields[6],
                            "note": "",
                        }
                    )
            continue

        # normal: "22/tcp open ssh OpenSSH 8.2p1 Ubuntu"
        port = re.match(r"^(\d{1,5})/(tcp|udp)\s+(\w+)\s+(\S+)\s*(.*)$", line)
        if port and host:
            number, proto, state, service, version = port.groups()
            if state != "open":
                continue
            scan.services.append(
                {
                    "host": host,
                    "port": int(number),
                    "proto": proto,
                    "service": service,
                    "version": version.strip(),
                    "note": "",
                }
            )
    return scan


def parse_httpx(text: str) -> ParsedScan:
    scan = ParsedScan()
    for raw in text.splitlines():
        line = _ANSI.sub("", raw).strip()
        if not line:
            continue

        if line.startswith("{"):
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            url = data.get("url") or data.get("input") or ""
            host, port = _host_port(url)
            if not host:
                continue
            tech = data.get("webserver") or data.get("tech") or ""
            if isinstance(tech, list):
                tech = ", ".join(tech)
            status = data.get("status_code") or data.get("status-code") or ""
            title = data.get("title") or ""
            scan.services.append(
                {
                    "host": host,
                    "port": port,
                    "proto": "tcp",
                    "service": _scheme_service(url),
                    "version": str(tech),
                    "note": f"{status} {title}".strip(),
                }
            )
            continue

        parts = line.split()
        if not parts or "://" not in parts[0]:
            continue
        url = parts[0]
        host, port = _host_port(url)
        if not host:
            continue
        brackets = re.findall(r"\[([^\]]*)\]", line)
        status = brackets[0] if brackets else ""
        title = brackets[1] if len(brackets) > 1 else ""
        tech = ", ".join(brackets[2:]) if len(brackets) > 2 else ""
        scan.services.append(
            {
                "host": host,
                "port": port,
                "proto": "tcp",
                "service": _scheme_service(url),
                "version": tech,
                "note": f"{status} {title}".strip(),
            }
        )
    return scan


def parse_nuclei(text: str) -> ParsedScan:
    scan = ParsedScan()
    for raw in text.splitlines():
        line = _ANSI.sub("", raw).strip()
        if not line:
            continue

        if line.startswith("{"):
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            info = data.get("info", {}) if isinstance(data.get("info"), dict) else {}
            template = data.get("template-id") or data.get("templateID") or info.get("name", "finding")
            host = data.get("host") or data.get("matched-at") or data.get("matched_at") or ""
            scan.findings.append(
                {
                    "title": str(info.get("name") or template),
                    "severity": _sev(info.get("severity", "info")),
                    "host": host,
                    "evidence": str(data.get("matched-at") or data.get("matched_at") or ""),
                }
            )
            continue

        brackets = re.findall(r"\[([^\]]*)\]", line)
        if len(brackets) < 3:
            continue
        template = brackets[0]
        severity = brackets[2]
        rest = re.sub(r"^(\[[^\]]*\]\s*)+", "", line).strip()
        url = rest.split()[0] if rest else ""
        scan.findings.append(
            {
                "title": template,
                "severity": _sev(severity),
                "host": url,
                "evidence": rest,
            }
        )
    return scan


_PARSERS = {"nmap": parse_nmap, "httpx": parse_httpx, "nuclei": parse_nuclei}

SUPPORTED = tuple(_PARSERS)


def parse(tool: str, text: str) -> ParsedScan:
    parser = _PARSERS.get((tool or "").strip().lower())
    return parser(text or "") if parser else ParsedScan()
