from __future__ import annotations

from app.core.anthropic import identity


def _body(value: str) -> dict:
    return {
        "system": [
            {
                "type": "text",
                "text": (
                    f"x-anthropic-billing-header: cc_version=2.1.280; cch={value}; "
                    f"cc_prompt_id={value}; cc_turn_origin=sdk;"
                ),
            }
        ]
    }


def test_billing_values_are_session_scoped_and_expire_after_idle_ttl(monkeypatch):
    identity._billing_session_values.clear()
    clock = [100.0]
    monkeypatch.setattr(identity.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(identity, "_BILLING_SESSION_TTL_SECONDS", 10)
    first = identity.stabilize_billing_marker_for_session(_body("first"), "session-a")
    assert identity.stabilize_billing_marker_for_session(_body("second"), "session-a") == first
    assert identity.stabilize_billing_marker_for_session(_body("other"), "session-b") == _body("other")
    assert identity.stabilize_billing_marker_for_session(_body("second"), None) == _body("second")

    clock[0] = 111.0
    assert identity.stabilize_billing_marker_for_session(_body("new"), "session-a") == _body("new")
    identity._billing_session_values.clear()


def test_billing_session_cache_evicts_lru(monkeypatch):
    identity._billing_session_values.clear()
    monkeypatch.setattr(identity, "_BILLING_SESSION_MAX_ENTRIES", 2)
    identity.stabilize_billing_marker_for_session(_body("a"), "session-a")
    identity.stabilize_billing_marker_for_session(_body("b"), "session-b")
    identity.stabilize_billing_marker_for_session(_body("a2"), "session-a")
    identity.stabilize_billing_marker_for_session(_body("c"), "session-c")
    assert identity.stabilize_billing_marker_for_session(_body("b2"), "session-b") == _body("b2")
    assert len(identity._billing_session_values) == 2
    identity._billing_session_values.clear()
