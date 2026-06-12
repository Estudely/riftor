"""Built-in engagement templates registry."""

from __future__ import annotations

from riftor.engagement.templates import (
    ACTIVE_TEMPLATE_META_KEY,
    TEMPLATES,
    Template,
)


def test_constant_value():
    assert ACTIVE_TEMPLATE_META_KEY == "template"


def test_expected_templates_present():
    assert set(TEMPLATES) >= {"webapp", "api", "network", "ad"}


def test_every_template_is_valid():
    for key, t in TEMPLATES.items():
        assert isinstance(t, Template)
        assert t.key == key
        assert t.stage in ("R", "I", "F", "T")
        assert t.methodology.strip()
        assert t.tools and all(isinstance(x, str) for x in t.tools)
        assert t.description.strip()


def test_engagement_set_and_get_template(engagement):
    assert engagement.active_template() is None
    engagement.set_template("webapp")
    assert engagement.active_template() == "webapp"
    # persists across reopen of the same workdir
    from riftor.engagement import Engagement
    reopened = Engagement(engagement.dir.parent)
    assert reopened.active_template() == "webapp"


def test_engagement_clear_template(engagement):
    engagement.set_template("api")
    engagement.set_template("")
    assert engagement.active_template() is None
