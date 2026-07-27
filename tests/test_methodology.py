"""Methodology checklist: seed, check, auto_tick."""

from __future__ import annotations

from riftor.engagement.methodology import (
    auto_tick_for_tool,
    default_methodology,
    format_methodology_block,
)


def test_default_methodology_has_expected_categories():
    items = default_methodology()
    assert len(items) >= 60
    cats = {i.category for i in items}
    assert {"Reconnaissance", "Authentication", "Injection", "API Security"} <= cats
    names = {i.name for i in items}
    assert "Port scanning" in names
    assert "SQL injection" in names
    assert "Vulnerability Identification" in names


def test_engagement_seeds_methodology(engagement):
    items = engagement.list_methodology()
    assert len(items) == len(default_methodology())
    done, total = engagement.methodology_progress()
    assert done == 0
    assert total == len(items)


def test_seed_is_idempotent(engagement):
    n = len(engagement.list_methodology())
    engagement.store.seed_methodology_if_empty()
    assert len(engagement.list_methodology()) == n


def test_check_methodology_by_name(engagement):
    assert engagement.check_methodology("Port scanning")
    items = {i.name: i for i in engagement.list_methodology()}
    assert items["Port scanning"].checked is True
    done, total = engagement.methodology_progress()
    assert done == 1 and total == len(items)


def test_check_methodology_substring(engagement):
    assert engagement.check_methodology("port scan")
    assert any(i.checked and "Port scanning" in i.name for i in engagement.list_methodology())


def test_check_methodology_with_notes(engagement):
    assert engagement.check_methodology("SQL injection", notes="confirmed via sqlmap")
    item = next(i for i in engagement.list_methodology() if i.name == "SQL injection")
    assert item.checked and "sqlmap" in item.notes


def test_check_unknown_returns_false(engagement):
    assert engagement.check_methodology("not a real checklist item") is False


def test_auto_tick_for_tool_mapping():
    assert auto_tick_for_tool("import_scan") == "Port scanning"
    assert auto_tick_for_tool("bash", "nmap -sV 10.0.0.5") == "Port scanning"
    assert auto_tick_for_tool("bash", "subfinder -d example.com") == "Subdomain enumeration"
    assert auto_tick_for_tool("record_finding") == "Vulnerability Identification"
    assert auto_tick_for_tool("read", "notes.txt") is None


def test_auto_tick_methodology(engagement):
    assert engagement.auto_tick_methodology("import_scan")
    assert any(i.name == "Port scanning" and i.checked for i in engagement.list_methodology())
    # already checked → no second tick needed, still returns False or True ok
    assert engagement.auto_tick_methodology("bash", "nmap -Pn target") is False


def test_format_methodology_block():
    items = default_methodology()[:3]
    # mark first checked
    from riftor.engagement.methodology import MethodologyItem
    checked = [
        MethodologyItem(items[0].category, items[0].name, checked=True, notes="done"),
        *items[1:],
    ]
    block = format_methodology_block(checked)
    assert "METHODOLOGY CHECKLIST (1/3 complete)" in block
    assert "[x] " + items[0].name in block
    assert "[ ] " + items[1].name in block


def test_list_and_check_tools(engagement, toolctx):
    import asyncio

    from riftor import tools

    async def _run():
        list_tool = tools.get("list_methodology")
        check_tool = tools.get("check_methodology")
        assert list_tool is not None and check_tool is not None
        listed = await list_tool.execute({}, toolctx)
        assert not listed.is_error
        assert "Port scanning" in listed.content or "Reconnaissance" in listed.content
        checked = await check_tool.execute({"name": "Port scanning"}, toolctx)
        assert not checked.is_error
        assert "Port scanning" in checked.content or "checked" in checked.content.lower()
        done, _ = engagement.methodology_progress()
        assert done >= 1

    asyncio.run(_run())
