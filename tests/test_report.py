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
    assert "needs-validation" in md and "found via sqlmap" in md
    assert "9.8" in md  # cvss derived


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
