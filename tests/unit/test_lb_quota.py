"""Golden quota math using the recorded 2026-07-01 Anthropic overage response."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.core.anthropic.quota_headers import capture_unified_headers, parse_unified_utilization
from tests.integration.test_anthropic_proxy import _OVERAGE_BILLING_HEADERS
from tests.unit.test_lb_cache import load_lb_cache


def test_recorded_headers_through_parser_and_quota_report():
    """Guard pure quota allocation math: missing tokens, resets, ties and partial windows.

    Existing request-boundary tests own transport and persistence; this recorded
    table guards allocation arithmetic they do not exercise, without a test seam.
    """
    lb = load_lb_cache()
    headers = capture_unified_headers({**_OVERAGE_BILLING_HEADERS, 'authorization': 'excluded'})
    assert headers == _OVERAGE_BILLING_HEADERS
    recorded = parse_unified_utilization(headers['anthropic-ratelimit-unified-5h-utilization'])
    assert recorded == 1.0
    for raw in ('nan', 'inf', '-1', 'bad', None):
        assert parse_unified_utilization(raw) is None

    def row(n, seat, five=None, weekly=None, tokens=10, evidence=None, account='a'):
        return {'id': n, 'account_id': account, 'ts': f'2026-07-01T00:{n:02}:00Z',
                'caller_seat': seat, 'room': 'lane', 'input_tokens': tokens,
                'output_tokens': 0 if tokens is not None else None,
                'cache_creation_tokens': 0, 'cache_read_tokens': 1_000_000,
                'unified_5h_utilization': five, 'unified_7d_utilization': weekly,
                'unified_headers': evidence}

    # All surrounding utilization values are synthetic baselines, not live measurements.
    rows = [row(0, 'baseline', .9, .2), row(1, 'alpha', tokens=10),
            row(2, 'beta', recorded, .3, tokens=30, evidence=headers),
            row(3, 'alpha', tokens=None), row(4, 'beta', 1.1, .4),
            row(5, 'alpha', .1, .1), row(6, 'beta', .2, .2),
            row(7, 'alpha', .3, .3, evidence={'anthropic-ratelimit-unified-5h-reset': 'old'}),
            row(8, 'beta', .4, .4, evidence={'anthropic-ratelimit-unified-5h-reset': 'new'}),
            row(9, 'tail'), row(0, 'other', .1, .1, account='b'),
            row(1, 'other', .15, .15, account='b')]
    start = datetime(2026, 7, 1, tzinfo=timezone.utc)
    end = datetime(2026, 7, 2, tzinfo=timezone.utc)
    data = lb.quota_report(rows, start, end)
    by_seat = {r['caller_seat']: r for r in data['accounts']}
    assert by_seat['alpha']['percent_5h'] == pytest.approx(17.5)
    assert by_seat['beta']['percent_5h'] == pytest.approx(22.5)
    assert by_seat['alpha']['percent_7d'] == pytest.approx(17.5)
    assert by_seat['beta']['percent_7d'] == pytest.approx(32.5)
    assert by_seat['other']['percent_5h'] == pytest.approx(5)
    assert data['top5'][0]['caller_seat'] == 'beta'
    assert data['coverage']['5h']['skipped_resets_or_drops'] == 2
    assert data['coverage']['5h']['request_share_intervals'] == 1
    assert data['coverage']['5h']['unmeasured_requests'] >= 2
    assert '%5h' in lb.quota_display(data)
    # A boundary inside the first interval keeps alpha's weight in the denominator.
    partial = lb.quota_report(rows, start.replace(minute=2), end)
    beta = next(r for r in partial['accounts'] if r['caller_seat'] == 'beta')
    assert beta['percent_5h'] == pytest.approx(22.5)
    assert partial['history_existed'] is True
    assert lb.quota_report([], start, end)['top5'] == []
