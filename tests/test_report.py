"""Report rendering: markdown, HTML, JSON, SARIF, and the executive summary."""

from __future__ import annotations

import json


def _seed(engagement):
    engagement.add_scope("example.com", "in")
    engagement.add_service(host="10.0.0.5", port=443, service="https", version="nginx")
    engagement.add_finding(
        title="SQL Injection", severity="high", host="10.0.0.5",
        evidence="' OR 1=1 --", recommendation="parameterize",
        tags="needs-validation", notes="found via sqlmap",
        cvss="CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H",
    )


def test_markdown_has_exec_summary_and_tags(engagement):
    from riftor.engagement.report import build_markdown, report_data

    _seed(engagement)
    md = build_markdown(report_data(engagement))
    assert "Executive summary" in md
    # tags/notes are markdown-escaped inline (issue #119); the hyphen in
    # "needs-validation" is backslash-escaped, so match on the safe substrings.
    assert "needs" in md and "validation" in md and "found via sqlmap" in md
    assert "9.8" in md  # cvss derived


def test_markdown_evidence_cannot_break_out_of_fence(engagement):
    """A hostile scan banner containing ``` must not escape the code fence and
    inject live markdown/HTML into the report (issue #119)."""
    from riftor.engagement.report import build_markdown, report_data

    engagement.add_finding(
        title="XSS", severity="medium", host="10.0.0.9",
        evidence="banner\n```\n<img src=x onerror=alert(1)>",
    )
    md = build_markdown(report_data(engagement))
    # The injected payload must stay inside a fence: the opening fence is longer
    # than the 3-backtick run in the evidence, so the inner ``` can't close it.
    assert "````" in md, "fence was not widened to contain the inner backticks"
    # The <img> must appear verbatim inside the block, never as a bare tag line
    # that a renderer would treat as HTML at the document's top level.
    assert "<img src=x onerror=alert(1)>" in md


def test_markdown_inline_fields_escape_html(engagement):
    """Notes/recommendation are escaped so a hostile value can't inject HTML."""
    from riftor.engagement.report import build_markdown, report_data

    engagement.add_finding(
        title="IDOR", severity="low", host="10.0.0.1",
        notes="<script>alert(1)</script>",
    )
    md = build_markdown(report_data(engagement))
    assert "<script>alert(1)</script>" not in md
    assert "\\<script\\>" in md  # angle brackets backslash-escaped


def test_sarif_host_uri_is_valid(engagement):
    """SARIF artifactLocation.uri must be a URI reference, not a bare host."""
    from riftor.engagement.report import build_sarif, report_data

    _seed(engagement)
    sarif = json.loads(build_sarif(report_data(engagement)))
    loc = sarif["runs"][0]["results"][0]["locations"][0]
    uri = loc["physicalLocation"]["artifactLocation"]["uri"]
    assert uri == "//10.0.0.5"  # authority-form, not the bare IP


def test_sarif_security_severity_from_qualitative(engagement):
    """A finding with no CVSS vector still gets a non-zero security-severity
    derived from its qualitative severity (issue #124)."""
    from riftor.engagement.report import build_sarif, report_data

    engagement.add_finding(title="Critical thing", severity="critical", host="h1")
    sarif = json.loads(build_sarif(report_data(engagement)))
    rule = sarif["runs"][0]["tool"]["driver"]["rules"][0]
    assert float(rule["properties"]["security-severity"]) >= 9.0


def test_sarif_rule_collision_keeps_max_severity(engagement):
    """Two findings sharing a title collapse to one rule; the higher score wins
    instead of first-write clobbering (issue #124)."""
    from riftor.engagement.report import build_sarif, report_data

    engagement.add_finding(
        title="SQLi", severity="low", host="a",
        cvss="CVSS:3.1/AV:N/AC:L/PR:L/UI:N/S:U/C:N/I:L/A:N",  # ~4.3
    )
    engagement.add_finding(
        title="SQLi", severity="critical", host="b",
        cvss="CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H",  # 9.8
    )
    sarif = json.loads(build_sarif(report_data(engagement)))
    rules = {r["id"]: r for r in sarif["runs"][0]["tool"]["driver"]["rules"]}
    assert float(rules["SQLi"]["properties"]["security-severity"]) == 9.8


def test_json_report(engagement):
    from riftor.engagement.report import build_json, report_data

    _seed(engagement)
    payload = json.loads(build_json(report_data(engagement)))
    assert payload["tool"] == "riftor"
    assert payload["findings"][0]["cvss_score"] == 9.8
    assert payload["summary"]["total"] == 1


def test_sarif_report(engagement):
    from riftor.engagement.report import build_sarif, report_data

    _seed(engagement)
    sarif = json.loads(build_sarif(report_data(engagement)))
    assert sarif["version"] == "2.1.0"
    run = sarif["runs"][0]
    assert run["tool"]["driver"]["name"] == "riftor"
    assert run["results"][0]["level"] == "error"  # high → error


def test_write_reports_all(engagement, tmp_workdir):
    from riftor.engagement.report import write_reports

    _seed(engagement)
    paths = write_reports(engagement, "all")
    suffixes = sorted(p.suffix for p in paths)
    assert suffixes == [".html", ".json", ".md", ".sarif"]
    assert all(p.exists() for p in paths)


def test_unknown_format_raises(engagement):
    import pytest

    from riftor.engagement.report import write_reports

    with pytest.raises(ValueError):
        write_reports(engagement, "pdf")


def test_exec_summary_empty(engagement):
    from riftor.engagement.report import report_data

    data = report_data(engagement)
    assert "No findings" in data["exec_summary"]
