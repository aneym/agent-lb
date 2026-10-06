from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def reservation_store(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_LB_RESERVATIONS_FILE", str(tmp_path / "reservations.json"))


def reservation_request(job: str, *, pinned: bool = False) -> dict:
    payload = {
        "job": job, "class": "implement", "host": "test-host", "ttl_s": 1200, "pinned": pinned,
        "candidates": [
            {"rung": "first", "seat": "first-seat", "model": "first-model", "capacity_key": "first", "capacity": 1},
            {"rung": "second", "seat": "second-seat", "model": "second-model", "capacity_key": "second", "capacity": 1},
        ],
    }
    if pinned:
        payload["candidates"] = payload["candidates"][:1]
    return payload


@pytest.mark.asyncio
async def test_pinned_reservation_rejects_multiple_candidates(async_client, reservation_store):
    payload = reservation_request("invalid")
    payload["pinned"] = True
    response = await async_client.post("/api/pools/reservations", json=payload)
    assert response.status_code == 422
    listing = await async_client.get("/api/pools/reservations")
    assert listing.json()["live"] == []


@pytest.mark.asyncio
async def test_pinned_reservation_waits_on_first_candidate_while_unpinned_overflows(async_client, reservation_store):
    first = await async_client.post("/api/pools/reservations", json=reservation_request("holder", pinned=True))
    assert first.status_code == 200
    assert first.json()["status"] == "reserved" and first.json()["seat"] == "first-seat"

    pinned = await async_client.post("/api/pools/reservations", json=reservation_request("pinned", pinned=True))
    assert pinned.status_code == 200
    body = pinned.json()
    assert body["status"] == "wait", body
    assert "seat" not in body and "reservation_id" not in body
    assert [row["rung"] for row in body["full"]] == ["first"]
    listing = await async_client.get("/api/pools/reservations")
    assert [row["job"] for row in listing.json()["live"]] == ["holder"]

    unpinned = await async_client.post("/api/pools/reservations", json=reservation_request("unpinned"))
    assert unpinned.status_code == 200
    body = unpinned.json()
    assert (body["status"], body["seat"], body["overflow_from"]) == ("reserved", "second-seat", ["first"])


@pytest.mark.asyncio
@pytest.mark.parametrize("candidates", [None, [], {}, [None], [{"rung": "first"}]])
async def test_pinned_reservation_rejects_invalid_candidates(async_client, reservation_store, candidates):
    payload = reservation_request("invalid", pinned=True)
    payload["candidates"] = candidates
    response = await async_client.post("/api/pools/reservations", json=payload)
    assert response.status_code == 422
    listing = await async_client.get("/api/pools/reservations")
    assert listing.json()["live"] == []
