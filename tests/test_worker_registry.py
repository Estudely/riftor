"""Worker registry: parse bundled worker agent markdown specs."""

from __future__ import annotations

from pathlib import Path

from riftor.workers.registry import WorkerSpec, _parse_md, get_worker, list_workers


def test_list_bundled_workers():
    workers = list_workers()
    names = {w.name for w in workers}
    assert names >= {"recon", "scout", "tester", "analyst", "exploiter"}
    for w in workers:
        assert isinstance(w, WorkerSpec)
        assert w.description.strip()
        assert w.tools
        assert w.prompt.strip()


def test_get_worker_recon():
    w = get_worker("recon")
    assert w is not None
    assert w.name == "recon"
    assert "bash" in w.tools
    assert "webfetch" in w.tools
    assert "reconnaissance" in w.prompt.lower() or "recon" in w.prompt.lower()


def test_get_worker_case_insensitive():
    assert get_worker("Recon") is not None
    assert get_worker("ANALYST") is not None


def test_get_unknown_worker():
    assert get_worker("not-a-worker") is None


def test_parse_md_frontmatter(tmp_path: Path):
    path = tmp_path / "custom.md"
    path.write_text(
        "---\n"
        "name: custom\n"
        "description: A custom worker\n"
        "model: ollama_chat/llama3\n"
        "tools: [bash, read]\n"
        "---\n"
        "You are a custom worker.\n",
        encoding="utf-8",
    )
    spec = _parse_md(path)
    assert spec is not None
    assert spec.name == "custom"
    assert spec.description == "A custom worker"
    assert spec.model == "ollama_chat/llama3"
    assert spec.tools == ("bash", "read")
    assert "custom worker" in spec.prompt.lower()


def test_parse_md_defaults_tools_from_name(tmp_path: Path):
    path = tmp_path / "recon.md"
    path.write_text(
        "---\nname: recon\ndescription: recon worker\n---\nDo recon.\n",
        encoding="utf-8",
    )
    spec = _parse_md(path)
    assert spec is not None
    assert "bash" in spec.tools


def test_parse_md_rejects_missing_frontmatter(tmp_path: Path):
    path = tmp_path / "plain.md"
    path.write_text("no frontmatter here\n", encoding="utf-8")
    assert _parse_md(path) is None
