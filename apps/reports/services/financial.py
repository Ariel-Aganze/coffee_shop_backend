from decimal import Decimal

from django.db.models import Sum

from apps.expenses.models import Expense, ExpenseStatus
from apps.sales.models import Sale, SaleItem, SaleStatus

from .common import branch_scope, end_of_day, parse_range, start_of_day


def _cogs_for_currency(sales_qs, currency: str) -> Decimal:
    """
    Sum of (current cost_price × quantity) for every SaleItem in this currency.

    NOTE: This uses each product's CURRENT cost_price, not the cost at the
    moment of sale. That is a Phase 2 enhancement (add `cost_price_snapshot`
    to SaleItem). It is good enough for now and documented on the response.
    """
    total = Decimal("0")
    items = (
        SaleItem.objects
        .filter(sale__in=sales_qs.filter(currency_code=currency))
        .select_related("product")
    )
    for item in items:
        price_row = item.product.current_price()
        if price_row and price_row.cost_price:
            total += price_row.cost_price * item.quantity
    return total


def financial_summary(request) -> dict:
    """
    REP-015: revenue, COGS, gross profit, expenses (per currency).
    BRULE-015: does NOT mix revenue / COGS / expenses into a net profit.
    """
    f, t = parse_range(request)
    scoped = branch_scope(request)

    # ---------------- Sales base queryset ----------------
    sales_qs = Sale.objects.filter(
        business_id=request.user.business_id,
        status__in=[SaleStatus.COMPLETED, SaleStatus.REFUNDED],
        created_at__gte=start_of_day(f),
        created_at__lte=end_of_day(t),
    )
    if scoped is not None:
        sales_qs = sales_qs.filter(branch_id__in=scoped)

    # ---------------- Revenue per currency ----------------
    revenue_rows = (
        sales_qs
        .values("currency_code")
        .annotate(revenue=Sum("total_amount"))
        .order_by("currency_code")
    )

    # ---------------- COGS per currency ----------------
    cogs_by_currency = {
        row["currency_code"]: _cogs_for_currency(sales_qs, row["currency_code"])
        for row in revenue_rows
    }

    # ---------------- Expenses (approved only) per currency ----------------
    expenses_qs = Expense.objects.filter(
        business_id=request.user.business_id,
        status=ExpenseStatus.APPROVED,
        expense_date__gte=f,
        expense_date__lte=t,
    )
    if scoped is not None:
        expenses_qs = expenses_qs.filter(branch_id__in=scoped)

    expenses_by_currency = {
        row["currency_code"]: row["total"] or Decimal("0")
        for row in expenses_qs.values("currency_code").annotate(total=Sum("amount"))
    }

    # ---------------- Assemble results ----------------
    results = []
    for row in revenue_rows:
        currency = row["currency_code"]
        revenue = row["revenue"] or Decimal("0")
        cogs = cogs_by_currency.get(currency, Decimal("0"))
        gross_profit = revenue - cogs
        expenses = expenses_by_currency.get(currency, Decimal("0"))
        operating_result = gross_profit - expenses

        results.append({
            "currency_code": currency,
            "revenue": str(revenue),
            "cost_of_goods_sold": str(cogs),
            "gross_profit": str(gross_profit),
            "operating_expenses": str(expenses),
            "operating_result": str(operating_result),
            "note": (
                "Operating result = gross profit − operating expenses. "
                "This is NOT net profit; taxes, depreciation and other "
                "costs are not included (BRULE-015)."
            ),
        })

    return {
        "period": {"from": f.isoformat(), "to": t.isoformat()},
        "results": results,
    }