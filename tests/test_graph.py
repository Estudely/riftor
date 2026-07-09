"""Attack graph: build_graph, to_mermaid, write_graph."""

from __future__ import annotations

from riftor.engagement.graph import build_graph, to_mermaid, write_graph


def test_empty_graph(engagement):
    graph = build_graph(engagement)
    assert graph == {"nodes": [], "edges": []}
    mermaid = to_mermaid(graph)
    assert mermaid.startswith("flowchart TD")
    path = write_graph(engagement)
    assert path.name == "graph.mmd"
    assert path.exists()
    assert path.read_text(encoding="utf-8") == mermaid


def test_host_service_finding_edges(engagement):
    engagement.add_service(host="10.0.0.5", port=443, service="https")
    engagement.add_finding(
        title="SQL Injection",
        severity="high",
        host="10.0.0.5",
        stage="I",
    )
    engagement.add_finding(
        title="Open SSH",
        severity="low",
        host="10.0.0.5",
        stage="R",
    )

    graph = build_graph(engagement)
    kinds = {n["kind"] for n in graph["nodes"]}
    assert kinds >= {"host", "service", "finding", "stage"}

    edge_kinds = {e["kind"] for e in graph["edges"]}
    assert "hosts" in edge_kinds
    assert "on_host" in edge_kinds
    assert "stage_order" in edge_kinds

    host_nodes = [n for n in graph["nodes"] if n["kind"] == "host"]
    assert len(host_nodes) == 1
    assert host_nodes[0]["label"] == "10.0.0.5"


def test_mermaid_contains_flowchart(engagement):
    engagement.add_service(host="example.com", port=80, service="http")
    engagement.add_finding(title="XSS", severity="medium", host="example.com", stage="F")

    mermaid = to_mermaid(build_graph(engagement))
    assert "flowchart TD" in mermaid
    assert "-->" in mermaid
    assert "example_com" in mermaid or "host_example" in mermaid
