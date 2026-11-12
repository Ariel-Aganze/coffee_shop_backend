from decimal import Decimal

from django.db.models import Count, DecimalField, Sum, Value
from django.db.models.functions import Coalesce

from apps.expenses.models import Expense, ExpenseStatus
from apps.sales.models import Sale, SaleStatus

from .common import (
    end_of_day,
    parse_range,
    recognized_sales,
    refunds_by,
    start_of_day,
)


ZERO = Value(Decimal("0"), output_field=DecimalField(max_digits=18, decimal_places=4))


def branch_comparison(request) -> dict:
    """
    DASH-013, REP-005: side-by-side metrics per branch.
    """
    from apps.accounts.access import has_business_wide_access

    if not has_business_wide_access(request.user):
        from rest_framework.exceptions import PermissionDenied
        raise PermissionDenied(
            "Only users with access to all branches can compare branches."
        )

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

    # Only fully paid sales count; refunds are netted out of revenue.
    sales_qs = recognized_sales(sales_qs)
    sales_rows = list(
        sales_qs.values("branch_id", "branch__name", "currency_code")
        .annotate(
            gross_sales=Coalesce(Sum("total_amount"), ZERO),
            transactions=Count("id"),
        )
        .order_by()
    )
    refunds = refunds_by(sales_qs, "branch_id", "currency_code")
    for r in sales_rows:
        r["refunds"] = refunds.get((r["branch_id"], r["currency_code"]), Decimal("0"))
        r["revenue"] = r["gross_sales"] - r["refunds"]
    sales_rows.sort(key=lambda r: r["revenue"], reverse=True)

    # Keyed by currency too, so amounts in different currencies never mix.
    expenses_by_branch = {
        (r["branch_id"], r["currency_code"]): r["total"] or Decimal("0")
        for r in expenses_qs.values("branch_id", "currency_code")
        .annotate(total=Sum("amount")).order_by()
    }

    return {
        "period": {"from": f.isoformat(), "to": t.isoformat()},
        "results": [
            {
                "branch_id": r["branch_id"],
                "branch_name": r["branch__name"],
                "currency_code": r["currency_code"],
                "gross_sales": str(r["gross_sales"]),
                "refunds": str(r["refunds"]),
                "revenue": str(r["revenue"]),
                "transactions": r["transactions"],
                "average_transaction_value": str(
                    (r["revenue"] / r["transactions"]) if r["transactions"] else 0
                ),
                "expenses": str(expenses_by_branch.get(
                    (r["branch_id"], r["currency_code"]), Decimal("0"),
                )),
            }
            for r in sales_rows
        ],
    }