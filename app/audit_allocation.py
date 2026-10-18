from __future__ import annotations

from app.audit_pricing import CLASSES


def empty():
    return {
        "requests": 0,
        "tokens": 0,
        "usd": 0.0,
        "quota_points": 0.0,
        "quota_5h_points": 0.0,
        "token_classes": dict.fromkeys(CLASSES, 0),
        "usd_classes": {},
    }


def merge(target, value):
    for key in ("requests", "tokens", "usd"):
        target[key] += value[key]
    for key in ("quota_points", "quota_5h_points"):
        target[key] = target.get(key, 0) + value.get(key, 0)
    for key in ("token_classes", "usd_classes"):
        for name, amount in value[key].items():
            target[key][name] = target[key].get(name, 0) + amount


def allocate(value, agents):

    positive = [agent for agent in agents if agent.get("weight", 0) > 0]
    if not positive:
        return [({"key": "lead", "seat": "lead", "kind": "lead"}, value)]
    largest = max(positive, key=lambda agent: agent["weight"])
    ordered = [agent for agent in positive if agent is not largest] + [largest]
    total = sum(agent["weight"] for agent in positive)
    remainder = {**value, "token_classes": value["token_classes"].copy(), "usd_classes": value["usd_classes"].copy()}
    allocated = []
    for agent in ordered[:-1]:
        fraction = agent["weight"] / total
        piece = {
            key: value.get(key, 0) * fraction
            for key in ("requests", "tokens", "usd", "quota_points", "quota_5h_points")
        }
        piece.update(
            {
                key: {name: amount * fraction for name, amount in value[key].items()}
                for key in ("token_classes", "usd_classes")
            }
        )
        for key in ("requests", "tokens", "usd", "quota_points", "quota_5h_points"):
            remainder[key] = remainder.get(key, 0) - piece[key]
        for key in ("token_classes", "usd_classes"):
            for name, amount in piece[key].items():
                remainder[key][name] -= amount
        allocated.append((agent, piece))
    return allocated + [(largest, remainder)]
