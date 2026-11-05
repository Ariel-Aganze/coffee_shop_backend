from decimal import Decimal

from django.db.models import Count, DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce

from apps.expenses.models import Expense, ExpenseStatus

from .common import branch_scope, end_of_day, parse_range, start_of_day


ZERO = Value(Decimal("0"), output_field=DecimalField(max_digits=18, decimal_places=4))


def expenses_report(request) -> dict:
    """
    REP-013: expenses grouped by category, currency, and status.
    """
    f, t = parse_range(request)
    qs = Expense.objects.filter(
        business_id=request.user.business_id,
        expense_date__gte=f,
        expense_date__lte=t,
    )
    scoped = branch_scope(request)
    if scoped is not None:
        qs = qs.filter(branch_id__in=scoped)
    if request.query_params.get("status"):
        qs = qs.filter(status=request.query_params["status"])

    rows = (
        qs.values("category__name", "currency_code", "status")
        .annotate(
            total=Coalesce(Sum("amount"), ZERO),
            count=Count("id"),
        )
        .order_by("category__name", "currency_code", "status")
    )

    grand_totals = (
        qs.filter(status=ExpenseStatus.APPROVED)
        .values("currency_code")
        .annotate(total=Coalesce(Sum("amount"), ZERO))
    )

    return {
        "period": {"from": f.isoformat(), "to": t.isoformat()},
        "results": [
            {
                "category": r["category__name"],
                "currency_code": r["currency_code"],
                "status": r["status"],
                "total": str(r["total"] or Decimal("0")),
                "count": r["count"],
            }
            for r in rows
        ],
        "approved_totals": [
            {
                "currency_code": r["currency_code"],
                "total": str(r["total"] or Decimal("0")),
            }
            for r in grand_totals
        ],
    }