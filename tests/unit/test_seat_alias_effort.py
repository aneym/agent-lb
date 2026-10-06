"""Pure alias parser/ranker edge cases: numeric versions, effort suffixes, absent
families and readmission ownership. The HTTP bridge has a separate effort contract.
"""
import json
import runpy
from pathlib import Path

import pytest

from app.modules.proxy.api import resolve_ccgpt_model

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("effort", ["low", "medium", "high", "xhigh"])
def test_effort_alias_uses_base_resolution_and_bridge_effort(effort):
    resolve = runpy.run_path(str(ROOT / "clients/route"))["resolve_alias"]
    table = json.loads((ROOT / "config/coding-agents/routing-table.json").read_text())
    # Numeric order, not lexical order; the retired generation must not win.
    served = ["gpt-6.9-sol", "gpt-6.10-sol", "gpt-5.6-sol"]
    assert resolve(table, f"sol-latest-{effort}", served)[0] == "gpt-6.10-sol"
    assert resolve(table, f"sol-latest-{effort}", [])[0] is None
    base = resolve_ccgpt_model("sol-latest")
    assert base is not None
    assert resolve_ccgpt_model(f"sol-latest-{effort}") == (base[0], effort)
    assert resolve(table, f"astra-latest-{effort}", ["gpt-6-astra"], "gpt-implementer")[0] is None
    assert resolve(table, f"missing-latest-{effort}", served)[0] is None
