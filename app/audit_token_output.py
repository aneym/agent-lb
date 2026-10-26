from __future__ import annotations

import html


def text_report(report):
    lines = [
        f"Window: {report['window']['since']} to {report['window']['until']}",
        f"Receipts: {report['totals']['requests']:,}  Tokens: {report['totals']['tokens']:,}  "
        f"Quota: {report['totals'].get('quota_points', 0):,.2f} points / "
        f"{report['totals'].get('account_weeks', 0):.3f} account-weeks; "
        f"secondary list price: ${report['totals']['usd']:,.2f}",
        f"Pricing: {report['priced_by']}",
        "Lane coverage: "
        + (
            "n/a (no priced spend)"
            if report["attribution"]["coverage"] is None
            else f"{report['attribution']['coverage']:.1%}; no lane ${report['attribution']['unattributed_usd']:,.2f}"
        ),
    ]
    for pool, failures in report.get("failures_by_pool", {}).items():
        lines.append(f"Failures {pool}: statuses={failures['status_counts']} classes={failures['error_classes']}")
    for dim, values in report["by"].items():
        lines += [f"\n{dim.upper():<40} {'POINTS':>10} {'ACCT-WEEKS':>16} {'USD':>12}"]
        lines += [
            f"{v['name'][:40]:<40} {v.get('quota_points', 0):>10,.2f} {v.get('account_weeks', 0):>16,.3f} "
            f"{v['usd']:>12,.2f}"
            for v in values
        ]
    lines += ["\nEXCLUSIVE WASTE CANDIDATES (cache USD is write premium; first_write is not waste)"]
    lines += [
        f"{k:<25} {v.get('quota_points', 0):>10,.2f} ${v['usd']:>12,.2f} {v['share']:>8.1%}"
        for k, v in report["waste"].items()
    ]
    for name, summary in report.get("quota", {}).get("providers", {}).items():
        lines.append(f"{name}: {summary['quota_points']:.2f} points; headroom now {summary['headroom_now']:.2f}")
    lines += ["\nNotes:"] + report["notes"]
    return "\n".join(lines) + "\n"


def html_report(report):
    page = [
        '<!doctype html><html><meta charset="utf-8"><title>Token audit</title>',
        "<style>body{font:16px system-ui;margin:32px;max-width:1200px}table{border-collapse:collapse;width:100%}"
        "th,td{padding:8px;text-align:right;border-bottom:1px solid #ddd}th:first-child,td:first-child{text-align:left}"
        "</style><h1>Token audit</h1>",
        f"<p>Quota: {report['totals'].get('quota_points', 0):,.2f} points / "
        f"{report['totals'].get('account_weeks', 0):.3f} account-weeks; secondary list price: "
        f"${report['totals']['usd']:,.2f}</p>",
    ]
    for dim, values in report["by"].items():
        page.append(
            f"<h2>{html.escape(dim)}</h2><table><tr><th>Name</th><th>Quota points</th>"
            "<th>Account-weeks</th><th>USD</th></tr>"
        )
        for value in values:
            page.append(
                f"<tr><td>{html.escape(value['name'])}</td><td>{value.get('quota_points', 0):,.2f}</td>"
                f"<td>{value.get('account_weeks', 0):,.3f}</td><td>{value['usd']:,.2f}</td></tr>"
            )
        page.append("</table>")
    page.append(
        "<h2>Exclusive waste candidates</h2><p>Cache dollars are write premiums; first_write is not waste.</p>"
        "<table><tr><th>Kind</th><th>Requests</th><th>Write tokens</th><th>USD</th></tr>"
    )
    for kind, value in report["waste"].items():
        page.append(
            f"<tr><td>{kind}</td><td>{value.get('quota_points', 0):,.2f}</td><td>{value['write_tokens']:,}</td>"
            f"<td>{value['usd']:,.2f}</td></tr>"
        )
    return "".join(page) + "</table></html>"
