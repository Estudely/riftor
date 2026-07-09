"""Bug-bounty scope parsers (#52) — offline, no API calls."""

from __future__ import annotations

import json

from riftor.engagement.bounty_scope import (
    apply_bounty_scope,
    parse_bounty_file,
    parse_generic,
    parse_hackerone,
)


_H1_FIXTURE = {
    "data": [
        {
            "id": "1",
            "type": "structured-scope",
            "attributes": {
                "asset_identifier": "https://api.example.com",
                "asset_type": "URL",
                "eligible_for_submission": True,
            },
        },
        {
            "id": "2",
            "type": "structured-scope",
            "attributes": {
                "asset_identifier": "*.example.com",
                "asset_type": "WILDCARD",
                "eligible_for_submission": True,
            },
        },
        {
            "id": "3",
            "type": "structured-scope",
            "attributes": {
                "asset_identifier": "legacy.example.com",
                "asset_type": "URL",
                "eligible_for_submission": False,
            },
        },
        {
            "id": "4",
            "type": "structured-scope",
            "attributes": {
                "asset_identifier": "com.example.app",
                "asset_type": "GOOGLE_PLAY_APP_ID",
                "eligible_for_submission": True,
            },
        },
    ]
}


def test_parse_hackerone_splits_in_out_and_skips_apps():
    parsed = parse_hackerone(_H1_FIXTURE)
    assert "api.example.com" in parsed.in_scope
    assert "*.example.com" in parsed.in_scope
    assert "legacy.example.com" in parsed.out_of_scope
    assert any("google_play_app_id" in s for s in parsed.skipped)
    assert parsed.source == "hackerone"


def test_parse_hackerone_from_json_string():
    parsed = parse_hackerone(json.dumps(_H1_FIXTURE))
    assert "api.example.com" in parsed.in_scope


def test_parse_generic_line_list():
    text = """
    # comment
    in: app.example.com
    out: staging.example.com
    10.0.0.0/24
    """
    parsed = parse_generic(text)
    assert "app.example.com" in parsed.in_scope
    assert "10.0.0.0/24" in parsed.in_scope
    assert "staging.example.com" in parsed.out_of_scope


def test_parse_bounty_file_autodetects_json():
    parsed = parse_bounty_file(json.dumps(_H1_FIXTURE))
    assert parsed.source == "hackerone"
    assert "api.example.com" in parsed.in_scope


def test_apply_bounty_scope(engagement):
    parsed = parse_hackerone(_H1_FIXTURE)
    added_in, added_out = apply_bounty_scope(engagement, parsed)
    assert added_in == 2
    assert added_out == 1
    ins = {t.raw for t in engagement.scope.in_scope}
    outs = {t.raw for t in engagement.scope.out_of_scope}
    assert "api.example.com" in ins
    assert "*.example.com" in ins
    assert "legacy.example.com" in outs
