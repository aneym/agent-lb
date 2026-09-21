from __future__ import annotations

import contextlib
import io
import json
import os
import runpy
import shutil
import socket
import subprocess
import sys
from datetime import datetime, timezone
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "config/coding-agents"
FIXTURE = ROOT / "tests/fixtures/coding-agent-routing/api_accounts.json"


class CodingAgentRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        local = ROOT / ".local"
        local.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=local)
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        shutil.copyfile(FIXTURE, self.home / "api_accounts.json")
        self.env = patch.dict(os.environ, {
            "HOME": str(self.home), "ROUTE_FIXTURE_DIR": str(self.home),
            "ROUTE_TABLE": str(SOURCE / "routing-table.json"),
            "ROUTE_STATE": str(self.home / "state.json"),
            "ROUTE_LEDGER": str(self.home / "dispatch.jsonl"),
        }, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.no_network = patch.object(socket, "create_connection", side_effect=AssertionError("network forbidden"))
        self.no_network.start()
        self.addCleanup(self.no_network.stop)
        self.path = patch.object(sys, "path", [str(SOURCE), *sys.path])
        self.path.start()
        self.addCleanup(self.path.stop)
        self.route = runpy.run_path(str(SOURCE / "route"))

    def run_cli(self, *args: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = self.route["main"](list(args))
            except SystemExit as failure:
                code = failure.code
        return code, out.getvalue(), err.getvalue()

    def accounts(self, body: dict) -> None:
        (self.home / "api_accounts.json").write_text(json.dumps(body))

    def run_seat_guard(self, tool_input: dict, snapshot: object | None) -> tuple[int, str, str]:
        return self.run_seat_guard_raw(json.dumps({"tool_name": "Agent", "tool_input": tool_input}), snapshot)

    def run_seat_guard_raw(self, payload: str, snapshot: object | None) -> tuple[int, str, str]:
        snapshot_path = self.home / "limit-watch.json"
        if snapshot is not None:
            snapshot_path.write_text(json.dumps(snapshot))
        result = subprocess.run(
            [sys.executable, str(SOURCE / "seat-guard.py")],
            input=payload,
            text=True,
            capture_output=True,
            env={**os.environ, "LIMIT_WATCH_SNAPSHOT": str(snapshot_path)},
            check=False,
        )
        return result.returncode, result.stdout, result.stderr

    def limit_watch_snapshot(self, *, usable_count: int = 2, fable_eligible_usable: int = 2,
                            polled_at: str | None = None) -> dict:
        return {
            "polled_at": polled_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "reachable": True,
            "providers": {"anthropic": {"usable_count": usable_count}},
            "fable_eligible_usable": fable_eligible_usable,
        }

    def jev_response(self, *, difficulty: float = 2, money: float = .98) -> dict:
        import route_jev
        return {"answers": {
            "class": {"choice": "implement", "confidence": .96,
                      "probabilities": {name: int(name == "implement") for name in route_jev.CLASSES}},
            "difficulty": {"score": difficulty, "confidence": .9,
                           "probabilities": {str(i): int(i == difficulty) for i in range(5)}},
            "money_path": {"noul": money}, "needs_write": {"noul": .99},
        }}

    def test_blocked_pool_with_healthy_weekly_headroom_is_not_admitted(self) -> None:
        body = json.loads(FIXTURE.read_text())
        body["accounts"] = [body["accounts"][1]]
        body["accounts"][0]["usage"]["primaryRemainingPercent"] = 0
        self.accounts(body)
        code, _, error = self.run_cli("pick", "implement")
        self.assertEqual(code, 2)
        self.assertIn("anthropic-a primary", error)
        self.assertIn("primary 0% <= reserve 20%", error)
        code, out, _ = self.run_cli("pools", "--json")
        pool = json.loads(out)["pools"][0]
        self.assertEqual((pool["status"], pool["usableNow"], pool["headroomPercent"]), ("blocked", 0, 74))
        self.assertEqual(pool["minShortWindowPercent"], 0)
        body["accounts"][0]["usage"]["primaryRemainingPercent"] = 100
        body["accounts"][0]["status"] = "quota_exceeded"
        self.accounts(body)
        self.assertEqual(self.run_cli("pick", "review")[0], 2)

    def test_stale_state_is_refused_when_account_refresh_fails(self) -> None:
        (self.home / "state.json").write_text(json.dumps({
            "ts": "2026-01-01T00:00:00Z", "pools": {"pools": [
                {"id": "openai-codex", "status": "ok", "headroomPercent": 100, "usableNow": 4}]}}))
        self.accounts({"__status": 503})
        code, _, error = self.run_cli("pick", "implement")
        self.assertEqual(code, 2)
        self.assertIn("stale-state", error)
        self.assertIn("accounts-refresh-failed", error)
        shutil.copyfile(FIXTURE, self.home / "api_accounts.json")
        code, out, _ = self.run_cli("pick", "implement", "--json")
        self.assertEqual(code, 0)
        self.assertIn("stale-state refreshed", json.loads(out)["reason"])

    def test_unknown_data_is_refused_instead_of_assumed_healthy(self) -> None:
        body = json.loads(FIXTURE.read_text())
        for account in body["accounts"]:
            account["resetAtPrimary"] = None
            account["resetAtSecondary"] = None
            account["usage"] = {
                "primaryRemainingPercent": None, "secondaryRemainingPercent": None,
            }
        self.accounts(body)
        code, _, error = self.run_cli("pick", "implement")
        self.assertEqual(code, 2)
        self.assertIn("unknown window data", error)
        self.accounts({})
        self.assertIn("unknown-account-data", self.run_cli("pick", "implement")[2])

    def test_money_path_classify_maps_terra_astra_opus_and_logs_dispatch(self) -> None:
        import route_jev
        sent = []

        def answer(request, **kwargs):
            sent.append(json.loads(request.data))
            return io.StringIO(json.dumps(self.jev_response()))

        with patch.dict(os.environ, {"TYPESAFE_API_KEY": "fixture-not-a-credential"}), \
                patch.object(route_jev.urllib.request, "urlopen", side_effect=answer):
            code, out, error = self.run_cli("classify", "Fix billing receipts", "--paths", "app/billing.py", "--json")
            self.assertEqual((code, error), (0, ""))
            decision = json.loads(out)
            self.assertEqual((decision["seat"], decision["model"], decision["effort"]),
                             ("implementer", "gpt-5.6-terra", "medium"))
            self.assertEqual([v["model"] for v in decision["verifiers"]], ["gpt-6-astra", "claude-opus-5"])
            self.assertTrue(decision["verifiers"][0]["required"] and decision["verifiers"][0]["read_only"])
            self.assertFalse(decision["verifiers"][1]["required"])
            self.assertEqual(decision["limitingWindow"]["window"], "weekly")
            self.assertEqual(decision["jev_confidence"], .96)
            self.assertEqual(len(sent), 1)
            self.assertEqual(sent[0]["state"], {"task": "Fix billing receipts", "paths": ["app/billing.py"]})
            self.assertEqual([q["type"] for q in sent[0]["questions"].values()], ["choice", "score", "noul", "noul"])
            self.assertNotIn("fixture-not-a-credential", json.dumps(sent))
            code, out, _ = self.run_cli("dispatch-line", "Fix billing receipts")
            self.assertEqual(out, "[class:implement] seat=implementer model=gpt-5.6-terra effort=medium\n")
            record = json.loads((self.home / "dispatch.jsonl").read_text())
            self.assertEqual((record["event"], record["task_class"], record["subagent_type"]),
                             ("dispatch", "implement", "implementer"))
            self.assertEqual(len(record["decision"]["verifiers"]), 2)
            code, out, _ = self.run_cli("pick", "plan", "--task-text", "Plan RLS", "--json")
            self.assertEqual(code, 0)
            self.assertEqual(len(json.loads(out)["verifiers"]), 2)
            body = json.loads(FIXTURE.read_text())
            body["accounts"][1]["status"] = "quota_exceeded"
            self.accounts(body)
            code, out, error = self.run_cli("dispatch-line", "Fix billing receipts")
            self.assertEqual((code, error), (0, ""))
            lines = (self.home / "dispatch.jsonl").read_text().splitlines()
            self.assertIn("money-path Opus second read unavailable", json.loads(lines[-1])["decision"]["warnings"])
            self.assertEqual(len(lines), 2)

    def test_jev_unavailable_exits_3_without_dispatch_or_retry(self) -> None:
        import route_jev
        unavailable = subprocess.CompletedProcess(["jev"], 3, stdout="", stderr="JEV UNAVAILABLE (auth)")
        with patch.object(route_jev.shutil, "which", return_value="/usr/bin/jev"), \
                patch.object(route_jev.subprocess, "run", return_value=unavailable) as invoke:
            for command in ("classify", "dispatch-line"):
                code, out, error = self.run_cli(command, "Fix bug")
                self.assertEqual((code, out), (3, ""))
                self.assertIn("JEV UNAVAILABLE (cli unavailable)", error)
        self.assertEqual(invoke.call_count, 2)
        self.assertFalse((self.home / "dispatch.jsonl").exists())

    def test_difficulty_and_reserve_cannot_restore_old_opus_first_ranking(self) -> None:
        import route_jev
        with patch.dict(os.environ, {"TYPESAFE_API_KEY": "fixture"}), \
                patch.object(route_jev.urllib.request, "urlopen",
                             side_effect=lambda *a, **kw: io.StringIO(
                                 json.dumps(self.jev_response(difficulty=3, money=.1)))):
            code, out, _ = self.run_cli("classify", "Fix a complex renderer", "--json")
            self.assertEqual(code, 0)
            result = json.loads(out)
            self.assertEqual((result["model"], result["difficulty"]), ("gpt-5.6-sol", 4))
            self.assertEqual([v["model"] for v in result["verifiers"]], ["gpt-6-astra"])
        self.assertEqual(self.run_cli("pick", "implement", "--reserve", "65")[0], 2)
        self.assertEqual(self.run_cli("pick", "implement", "--reserve", "64")[0], 0)
        table = json.loads((SOURCE / "routing-table.json").read_text())
        self.assertEqual([e["model"] for e in table["classes"]["implement"]["chain"]],
                         ["gpt-5.6-terra", "gpt-5.6-sol", "gpt-5.6-sol", "glm-*", "claude-opus-5"])
        self.assertFalse(any(o["class"] == "unknown" for o in table["overrides"]))
        self.assertIn("**OpenAI builds; Astra validates.**", (SOURCE / "ROUTING.md").read_text())

    def test_pulse_counts_opus_sonnet_and_reports_missing_telemetry(self) -> None:
        pulse = runpy.run_path(str(SOURCE / "routing-pulse.py"))
        with patch("urllib.request.urlopen", return_value=io.StringIO(json.dumps({"seats": [
                {"model": "claude-fable-5", "requests": 10},
                {"model": "claude-opus-5", "requests": 20},
                {"model": "claude-sonnet-5", "requests": 30},
                {"model": "gpt-6-astra", "requests": 100}]}))):
            self.assertEqual(pulse["anthropic_requests"]("session123", 60)[0], 60)
        out = io.StringIO()
        with patch("urllib.request.urlopen", side_effect=OSError("offline")), \
                patch.object(sys, "stdin", io.StringIO('{"session_id":"session123"}')), \
                contextlib.redirect_stdout(out):
            pulse["main"]()
        self.assertEqual(out.getvalue(), "ROUTING PULSE: telemetry missing (session analytics unavailable)\n")
        with patch("urllib.request.urlopen", return_value=io.StringIO('{"seats":[{"model":"claude-opus-5"}]}')):
            self.assertIsNone(pulse["anthropic_requests"]("session123", 60)[0])
        self.assertNotIn("not rationed", (SOURCE / "seat-guard.py").read_text())

    def test_fable_telemetry_and_historical_fixtures_are_not_route_migrated(self) -> None:
        launcher = (ROOT / "clients/claude-lb-launch").read_text()
        pricing = (ROOT / "app/core/anthropic/pricing.py").read_text()
        pulse_test = (ROOT / "tests/unit/test_account_pulse.py").read_text()
        fixture = (ROOT / "clients/macos-menubar/Tests/AgentLBTests/Fixtures/request-logs.json").read_text()
        self.assertIn('FABLE_SCOPED_WEEKLY_QUOTA_KEY = "anthropic_fable_scoped_weekly"', launcher)
        self.assertIn('"claude-fable-5": AnthropicModelPrice(', pricing)
        self.assertIn('calls[0]["model"] == "claude-fable-5"', pulse_test)
        self.assertIn('"model":"claude-fable-5"', fixture)

    def test_provider_without_short_window_is_admitted_on_weekly_alone(self) -> None:
        code, out, error = self.run_cli("pick", "implement", "--json")
        self.assertEqual((code, error), (0, ""))
        result = json.loads(out)
        self.assertEqual((result["pool"], result["limitingWindow"]["window"]), ("openai-codex", "weekly"))

    def test_top_level_reset_marks_missing_percent_as_reported_unknown(self) -> None:
        body = json.loads(FIXTURE.read_text())
        openai = body["accounts"][0]
        openai["resetAtPrimary"] = "2026-09-28T00:00:00Z"
        self.accounts(body)
        code, out, error = self.run_cli("pools", "--json")
        self.assertEqual((code, error), (0, ""))
        openai_pool = next(pool for pool in json.loads(out)["pools"] if pool["id"] == "openai-codex")
        self.assertEqual((openai_pool["status"], openai_pool["usableNow"]), ("blocked", 0))
        self.assertEqual(openai_pool["limitingWindow"]["reason"], "unknown reported window percent")

        openai["resetAtPrimary"] = None
        self.accounts(body)
        code, out, error = self.run_cli("pools", "--json")
        self.assertEqual((code, error), (0, ""))
        openai_pool = next(pool for pool in json.loads(out)["pools"] if pool["id"] == "openai-codex")
        self.assertEqual((openai_pool["status"], openai_pool["usableNow"]), ("ok", 1))

    def test_selected_anthropic_quota_gates_model_without_blocking_general_pool(self) -> None:
        body = json.loads(FIXTURE.read_text())
        body["accounts"][0]["status"] = "quota_exceeded"
        anthropic = body["accounts"][1]
        anthropic["additionalQuotas"] = [{
            "quotaKey": "anthropic_top_thinking",
            "primaryWindow": {"usedPercent": 100, "resetAt": 1789948800, "windowMinutes": 300},
            "secondaryWindow": {"usedPercent": 20, "resetAt": 1790553600, "windowMinutes": 10080},
        }]
        self.accounts(body)

        code, _, error = self.run_cli("pick", "review")
        self.assertEqual(code, 2)
        self.assertIn("anthropic_top_thinking primary 0% <= reserve 20%", error)
        code, out, error = self.run_cli("pools", "--json")
        self.assertEqual((code, error), (0, ""))
        anthropic_pool = next(pool for pool in json.loads(out)["pools"] if pool["id"] == "anthropic-general")
        self.assertEqual((anthropic_pool["status"], anthropic_pool["usableNow"]), ("ok", 1))

        anthropic["additionalQuotas"][0]["primaryWindow"]["usedPercent"] = 10
        anthropic["additionalQuotas"][0]["secondaryWindow"]["usedPercent"] = 80
        self.accounts(body)
        code, _, error = self.run_cli("pick", "review")
        self.assertEqual(code, 2)
        self.assertIn("anthropic_top_thinking secondary 20% <= reserve 20%", error)

    def test_selected_fable_model_also_uses_scoped_weekly_quota(self) -> None:
        body = json.loads(FIXTURE.read_text())
        anthropic = body["accounts"][1]
        body["accounts"] = [anthropic]
        anthropic["fableEligible"] = True
        anthropic["additionalQuotas"] = [
            {"quotaKey": "anthropic_top_thinking",
             "primaryWindow": {"usedPercent": 10, "resetAt": 1789948800}},
            {"quotaKey": "anthropic_fable_scoped_weekly",
             "primaryWindow": {"usedPercent": 100, "resetAt": 1790553600}},
        ]
        self.accounts(body)
        table = json.loads((SOURCE / "routing-table.json").read_text())
        table["classes"]["review"]["chain"] = [
            {"seat": "fable-review", "model": "claude-fable-5", "effort": "medium"}
        ]
        table["pools"]["claude-fable-5"] = "anthropic-fable"
        local_table = self.home / "routing-table.json"
        local_table.write_text(json.dumps(table))
        with patch.dict(os.environ, {"ROUTE_TABLE": str(local_table)}):
            code, _, error = self.run_cli("pick", "review")
        self.assertEqual(code, 2)
        self.assertIn("anthropic_fable_scoped_weekly primary 0% <= reserve 20%", error)

    def test_blocked_verifier_pool_does_not_block_implement_pick(self) -> None:
        body = json.loads(FIXTURE.read_text())
        body["accounts"].append({
            "accountId": "glm-a", "provider": "glm", "status": "active",
            "resetAtPrimary": "2026-09-22T00:00:00Z", "resetAtSecondary": None,
            "usage": {"primaryRemainingPercent": 65, "secondaryRemainingPercent": None},
        })
        body["accounts"][0]["status"] = "quota_exceeded"
        body["accounts"][1]["status"] = "quota_exceeded"
        self.accounts(body)
        code, out, error = self.run_cli("pick", "implement", "--json")
        self.assertEqual((code, error), (0, ""))
        result = json.loads(out)
        self.assertIsNone(result["verifier"])
        self.assertEqual(result["verifier_reason"], "no admitted cross-vendor verifier")
        self.assertTrue(result["warnings"])

    def test_author_vendor_excludes_same_vendor_verifier(self) -> None:
        code, out, error = self.run_cli("pick", "verify", "--author-vendor", "openai", "--json")
        self.assertEqual((code, error), (0, ""))
        result = json.loads(out)
        self.assertEqual((result["model"], result["vendor"]), ("claude-opus-5", "anthropic"))
        self.assertNotEqual(result["verifier"]["vendor"], "openai")

    def test_seat_guard_stale_snapshot_denies(self) -> None:
        _, out, _ = self.run_seat_guard(
            {"subagent_type": "verifier"},
            self.limit_watch_snapshot(polled_at="2020-01-01T00:00:00Z"),
        )
        self.assertIn("permissionDecision\": \"deny", out)
        self.assertIn("stale snapshot", out)

    def test_seat_guard_missing_snapshot_denies(self) -> None:
        _, out, _ = self.run_seat_guard({"subagent_type": "opus-seat"}, None)
        self.assertIn("permissionDecision\": \"deny", out)
        self.assertIn("missing snapshot", out)

    def test_seat_guard_usable_under_two_denies(self) -> None:
        for name, snapshot, reason in (
            ("anthropic", self.limit_watch_snapshot(usable_count=1), "usable_count < 2"),
            ("fable-eligible", self.limit_watch_snapshot(fable_eligible_usable=1), "fable_eligible_usable < 2"),
        ):
            with self.subTest(name=name):
                _, out, _ = self.run_seat_guard({"model": "claude-sonnet-4-6"}, snapshot)
                self.assertIn("permissionDecision\": \"deny", out)
                self.assertIn(reason, out)

    def test_seat_guard_healthy_anthropic_seat_admits(self) -> None:
        _, out, _ = self.run_seat_guard(
            {"subagent_type": "plan-reviewer"}, self.limit_watch_snapshot(),
        )
        self.assertEqual(out, "")

    def test_seat_guard_non_anthropic_forwarder_admits_when_snapshot_missing(self) -> None:
        _, out, _ = self.run_seat_guard({"subagent_type": "implementer"}, None)
        self.assertEqual(out, "")

    def test_seat_guard_override_admits(self) -> None:
        with patch.dict(os.environ, {"SEAT_GUARD_ALLOW_ANTHROPIC": "1"}):
            _, out, _ = self.run_seat_guard({"subagent_type": "claude"}, None)
        self.assertEqual(out, "")
        record = json.loads((self.home / "dispatch.jsonl").read_text())
        self.assertTrue(record["anthropic_override"])

    def test_seat_guard_internal_error_denies(self) -> None:
        _, out, _ = self.run_seat_guard({"subagent_type": "security-reviewer"}, [])
        self.assertIn("permissionDecision\": \"deny", out)
        self.assertIn("internal error", out)

    def test_seat_guard_failure_class_fable_pin_ignores_snapshot_and_override(self) -> None:
        with patch.dict(os.environ, {"SEAT_GUARD_ALLOW_ANTHROPIC": "1"}):
            _, out, _ = self.run_seat_guard(
                {"subagent_type": "implementer", "model": "claude-fable-5"},
                self.limit_watch_snapshot(),
            )
        self.assertIn("permissionDecision\": \"deny", out)
        self.assertIn("pins a Fable model", out)

    def test_seat_guard_failure_class_unknown_seats_default_to_anthropic(self) -> None:
        for subagent in ("", "claude-code-guide", "statusline-setup",
                         "hookify:conversation-analyzer", "argent-environment-inspector"):
            with self.subTest(subagent=subagent):
                _, out, _ = self.run_seat_guard({"subagent_type": subagent}, None)
                self.assertIn("permissionDecision\": \"deny", out)
                self.assertIn("missing snapshot", out)

    def test_seat_guard_failure_class_normalizes_subagent_and_model(self) -> None:
        _, out, _ = self.run_seat_guard(
            {"subagent_type": " Explore ", "model": " GPT-5.6-terra "}, None,
        )
        self.assertIn("permissionDecision\": \"deny", out)
        record = json.loads((self.home / "dispatch.jsonl").read_text())
        self.assertEqual((record["subagent_type"], record["model"]), ("explore", "gpt-5.6-terra"))
        _, out, _ = self.run_seat_guard(
            {"subagent_type": " Implementer ", "model": " CLAUDE-SONNET-4-6 "}, None,
        )
        self.assertIn("permissionDecision\": \"deny", out)

    def test_seat_guard_failure_class_malformed_stdin_denies(self) -> None:
        _, out, _ = self.run_seat_guard_raw("{not-json", None)
        self.assertIn("permissionDecision\": \"deny", out)
        self.assertIn("malformed Agent hook input", out)

    def test_seat_guard_failure_class_forwarder_anthropic_model_is_quota_gated(self) -> None:
        _, out, _ = self.run_seat_guard(
            {"subagent_type": "implementer", "model": "claude-sonnet-4-6"}, None,
        )
        self.assertIn("permissionDecision\": \"deny", out)
        self.assertIn("missing snapshot", out)

    def test_seat_guard_failure_class_fork_is_recorded(self) -> None:
        _, out, _ = self.run_seat_guard({"subagent_type": " fork "}, None)
        self.assertEqual(out, "")
        record = json.loads((self.home / "dispatch.jsonl").read_text())
        self.assertEqual(record["subagent_type"], "fork")
        self.assertTrue(record["fork"])
