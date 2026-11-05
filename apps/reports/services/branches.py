from decimal import Decimal

from django.db.models import Count, DecimalField, Sum, Value
from django.db.models.functions import Coalesce

from apps.expenses.models import Expense, ExpenseStatus
from apps.sales.models import Sale, SaleStatus

from .common import end_of_day, parse_range, start_of_day


ZERO = Value(Decimal("0"), output_field=DecimalField(max_digits=18, decimal_places=4))


def branch_comparison(request) -> dict:
    """
    DASH-013, REP-005: side-by-side metrics per branch.
    """
    if request.user.role != "owner":
        from rest_framework.exceptions import PermissionDenied
        raise PermissionDenied("Only the owner can compare branches.")

    f, t = parse_range(request)
    sales_qs = Sale.objects.filter(
        business_id=request.user.business_id,
        status__in=[SaleStatus.COMPLETED, SaleStatus.REFUNDED],
        created_at__gte=start_of_day(f),
        created_at__lte=end_of_day(t),
    )
    expenses_qs = Expense.objects.filter(
        business_id=request.user.business_id,
        status=ExpenseStatus.APPROVED,
        expense_date__gte=f,
        expense_date__lte=t,
    )

    sales_rows = (
        sales_qs.values("branch_id", "branch__name", "currency_code")
        .annotate(
            revenue=Coalesce(Sum("total_amount"), ZERO),
            transactions=Count("id"),
        )
        .order_by("-revenue")
    )
    expenses_by_branch = {
        r["branch_id"]: r["total"] or Decimal("0")
        for r in expenses_qs.values("branch_id").annotate(total=Sum("amount"))
    }

    return {
        "period": {"from": f.isoformat(), "to": t.isoformat()},
        "results": [
            {
                "branch_id": r["branch_id"],
                "branch_name": r["branch__name"],
                "currency_code": r["currency_code"],
                "revenue": str(r["revenue"]),
                "transactions": r["transactions"],
                "average_transaction_value": str(
                    (r["revenue"] / r["transactions"]) if r["transactions"] else 0
                ),
                "expenses": str(expenses_by_branch.get(r["branch_id"], Decimal("0"))),
            }
            for r in sales_rows
        ],
    }