from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_policy_versions_observe_files_and_drafts_without_installing(async_client, db_setup, monkeypatch, tmp_path):
    del db_setup
    table_path = tmp_path / "routing-table.json"
    decider_path = tmp_path / "decider.json"
    original_table = {"classes": {"plan": {"chain": [{"seat": "a"}, {"seat": "b"}]}}, "aliases": {"a": "b"}, "retired": ["old"]}
    table_path.write_text(json.dumps(original_table))
    decider_path.write_text('{"decider":"jev"}')
    monkeypatch.setenv("ROUTE_TABLE", str(table_path))
    monkeypatch.setenv("OF_DECIDER_JSON", str(decider_path))

    first = await async_client.get("/api/routing-policy/versions")
    assert first.status_code == 200
    assert first.json()["activeVersion"] == 1
    assert first.json()["versions"][0]["counts"] == {"classes": 1, "options": 2, "aliases": 1, "retired": 1}
    assert (await async_client.get("/api/routing-policy/versions")).json()["versions"] == first.json()["versions"]

    changed_table = {**original_table, "classes": {"plan": {"chain": [{"seat": "c"}, {"seat": "b"}]}}}
    table_path.write_text(json.dumps(changed_table))
    second = (await async_client.get("/api/routing-policy/versions")).json()
    assert second["activeVersion"] == 2
    assert [(v["version"], v["state"], v["source"]) for v in second["versions"]] == [
        (2, "active", "file"), (1, "retired", "file")
    ]
    assert (await async_client.get("/api/routing-policy/versions")).json()["versions"] == second["versions"]

    created = await async_client.post("/api/routing-policy/drafts", json={"summary": "try c"})
    assert created.status_code == 200
    draft = created.json()
    assert draft["version"] == 3
    assert draft["routingTable"] == changed_table
    assert draft["decider"] == {"decider": "jev"}
    assert draft["diff"] == []

    patched = await async_client.patch("/api/routing-policy/drafts/3", json={"routingTable": original_table})
    assert patched.status_code == 200
    assert patched.json()["diff"] == [{"path": "/routingTable/classes/plan/chain/0/seat", "before": "c", "after": "a"}]
    assert (await async_client.get("/api/routing-policy/versions/3")).json()["diff"] == patched.json()["diff"]

    assert (await async_client.patch("/api/routing-policy/drafts/2", json={"summary": "no"})).status_code == 409
    assert (await async_client.delete("/api/routing-policy/drafts/2")).status_code == 409
    before = (table_path.read_bytes(), decider_path.read_bytes())
    refused = await async_client.post("/api/routing-policy/drafts/3/approve")
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "replay_required"
    assert (table_path.read_bytes(), decider_path.read_bytes()) == before
    assert (await async_client.delete("/api/routing-policy/drafts/3")).status_code == 204
    assert (await async_client.get("/api/routing-policy/versions/3")).status_code == 404
