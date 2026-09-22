from pathlib import Path
import runpy

CC = Path(__file__).resolve().parents[2] / "clients" / "cc"


def test_cc_defaults_to_auto_without_bypass():
    build = runpy.run_path(str(CC))["build_args"]
    assert build(["-p", "hello"]) == ["--permission-mode", "auto", "-p", "hello"]


def test_cc_preserves_explicit_permission_mode():
    build = runpy.run_path(str(CC))["build_args"]
    args = ["--permission-mode=plan", "-p", "hello"]
    assert build(args) == args


def test_cc_lane_loads_only_explicit_reviewed_plugin(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    plugin = tmp_path / ".agent-lb/plugins/codex-plugin-cc/plugins/codex"
    manifest = plugin / ".claude-plugin/plugin.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text('{}')
    build = runpy.run_path(str(CC))["build_args"]
    assert build(["--codex-lane"]) == ["--permission-mode", "auto", "--plugin-dir", str(plugin)]


def test_cc_lane_missing_plugin_fails_closed(monkeypatch, tmp_path):
    import pytest
    monkeypatch.setenv("HOME", str(tmp_path))
    build = runpy.run_path(str(CC))["build_args"]
    with pytest.raises(SystemExit, match="reviewed Codex plugin is missing"):
        build(["--codex-lane"])
