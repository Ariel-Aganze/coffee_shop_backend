from apps.cash_management.models import CashSession, CashSessionStatus

from .common import branch_scope, end_of_day, parse_range, start_of_day


def cash_reconciliation_report(request) -> dict:
    """
    REP-014: closed session summary.
    """
    f, t = parse_range(request)
    qs = CashSession.objects.filter(
        branch__business_id=request.user.business_id,
        opened_at__gte=start_of_day(f),
        opened_at__lte=end_of_day(t),
    ).select_related("branch", "cashier")

    scoped = branch_scope(request)
    if scoped is not None:
        qs = qs.filter(branch_id__in=scoped)

    if request.query_params.get("status"):
        qs = qs.filter(status=request.query_params["status"])

    return {
        "period": {"from": f.isoformat(), "to": t.isoformat()},
        "results": [
            {
                "session_id": s.id,
                "branch_name": s.branch.name,
                "cashier": s.cashier.username,
                "status": s.status,
                "opened_at": s.opened_at.isoformat(),
                "closed_at": s.closed_at.isoformat() if s.closed_at else None,
                "opening_balance": str(s.opening_balance),
                "expected_cash": str(s.expected_cash) if s.expected_cash is not None else None,
                "actual_cash": str(s.actual_cash) if s.actual_cash is not None else None,
                "difference": str(s.difference) if s.difference is not None else None,
                "currency_code": s.currency_code,
                "discrepancy_explanation": s.discrepancy_explanation,
            }
            for s in qs.order_by("-opened_at")
        ],
    }