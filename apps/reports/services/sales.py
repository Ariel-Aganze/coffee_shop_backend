"""
Sales reports (REP-001..REP-009).

Definitions used by every report here:
  - Only fully paid sales count (see `recognized_sales`); unpaid sales are
    listed separately in the summary, never as revenue.
  - `total_sales` = gross value of those sales (before refunds).
  - `refunds_total` = money refunded against them.
  - `net_sales` = total_sales − refunds_total.
  - Everything is grouped by currency, so a currency change never mixes
    amounts (CUR-017, REP-018).
"""
from decimal import Decimal

from django.db.models import Count, Q, Sum
from django.db.models.functions import Coalesce

from apps.accounts.constants import UserRole
from apps.payments.models import Payment
from apps.sales.models import Sale, SaleItem, SaleStatus

from .common import (
    ZERO,
    branch_scope,
    end_of_day,
    parse_range,
    recognized_sales,
    refunds_by,
    start_of_day,
    unpaid_sales,
)


def _base_sales_qs(request, *, currency_code=None):
    """All completed/refunded sales in range and scope (paid or not)."""
    f, t = parse_range(request)
    qs = Sale.objects.filter(
        business_id=request.user.business_id,
        status__in=[SaleStatus.COMPLETED, SaleStatus.REFUNDED],
        created_at__gte=start_of_day(f),
        created_at__lte=end_of_day(t),
    )
    scoped = branch_scope(request)
    if scoped is not None:
        qs = qs.filter(branch_id__in=scoped)
    # RBAC 4.2: a cashier's view of sales is limited to their own transactions.
    if request.user.role == UserRole.CASHIER:
        qs = qs.filter(cashier=request.user)
    if currency_code:
        qs = qs.filter(currency_code=currency_code)
    return qs, f, t


def _period(f, t) -> dict:
    return {"from": f.isoformat(), "to": t.isoformat()}


def _avg(amount: Decimal, count: int) -> Decimal:
    return (amount / count) if count else Decimal("0")


def sales_summary(request) -> dict:
    """
    REP-001..REP-004, REP-005, REP-006:
    Total sales, refunds, net sales, transaction count, average ticket.
    """
    base, f, t = _base_sales_qs(request)
    paid = recognized_sales(base)

    rows = (
        paid.values("currency_code", "currency_decimal_places")
        .annotate(
            total_sales=Coalesce(Sum("total_amount"), ZERO),
            transactions=Count("id"),
        )
        .order_by("currency_code")
    )
    refunds = refunds_by(paid, "currency_code")
    unpaid = {
        r["currency_code"]: r
        for r in unpaid_sales(base).values("currency_code").annotate(
            count=Count("id"), total=Coalesce(Sum("total_amount"), ZERO),
        ).order_by()
    }

    results = []
    for row in rows:
        code = row["currency_code"]
        tx = row["transactions"] or 0
        total = row["total_sales"] or Decimal("0")
        refunded = refunds.get((code,), Decimal("0"))
        net = total - refunded
        open_row = unpaid.pop(code, None)
        results.append({
            "currency_code": code,
            "currency_decimal_places": row["currency_decimal_places"],
            "total_sales": str(total),
            "refunds_total": str(refunded),
            "net_sales": str(net),
            "transactions": tx,
            "average_transaction_value": str(_avg(net, tx)),
            "unpaid_sales_count": open_row["count"] if open_row else 0,
            "unpaid_sales_total": str(open_row["total"] if open_row else Decimal("0")),
        })
    # Currencies that only have unpaid sales in this period.
    for code, open_row in unpaid.items():
        results.append({
            "currency_code": code,
            "total_sales": "0",
            "refunds_total": "0",
            "net_sales": "0",
            "transactions": 0,
            "average_transaction_value": "0",
            "unpaid_sales_count": open_row["count"],
            "unpaid_sales_total": str(open_row["total"]),
        })
    return {"period": _period(f, t), "results": results}


def _grouped_sales(request, keys: list[str], extra_values: list[str]):
    """Shared body for the by-branch / by-cashier reports."""
    base, f, t = _base_sales_qs(request)
    paid = recognized_sales(base)
    rows = list(
        paid.values(*keys, *extra_values)
        .annotate(
            total_sales=Coalesce(Sum("total_amount"), ZERO),
            transactions=Count("id"),
        )
        .order_by()
    )
    refunds = refunds_by(paid, *keys)
    for r in rows:
        r["refunds_total"] = refunds.get(tuple(r[k] for k in keys), Decimal("0"))
        r["net_sales"] = (r["total_sales"] or Decimal("0")) - r["refunds_total"]
    rows.sort(key=lambda r: r["net_sales"], reverse=True)
    return rows, f, t


def sales_by_branch(request) -> dict:
    rows, f, t = _grouped_sales(
        request, ["branch_id", "currency_code"], ["branch__name"],
    )
    return {
        "period": _period(f, t),
        "results": [
            {
                "branch_id": r["branch_id"],
                "branch_name": r["branch__name"],
                "currency_code": r["currency_code"],
                "total_sales": str(r["total_sales"]),
                "refunds_total": str(r["refunds_total"]),
                "net_sales": str(r["net_sales"]),
                "transactions": r["transactions"],
            }
            for r in rows
        ],
    }


def sales_by_product(request) -> dict:
    """
    Units and revenue per product, from fully paid sales.

    Fully refunded sales are excluded. Partial refunds are amount-based and
    can't be attributed to individual items, so they are not deducted here;
    the summary report's `refunds_total` covers them.
    """
    base, f, t = _base_sales_qs(request)
    paid = recognized_sales(base).exclude(status=SaleStatus.REFUNDED)
    rows = (
        SaleItem.objects.filter(sale__in=paid.values("pk"))
        .values(
            "product_id", "product_name", "sku",
            "sale__currency_code", "sale__currency_decimal_places",
        )
        .annotate(
            quantity_sold=Coalesce(Sum("quantity"), ZERO),
            revenue=Coalesce(Sum("line_total"), ZERO),
            orders=Count("sale_id", distinct=True),
        )
        .order_by("-revenue")
    )
    return {
        "period": _period(f, t),
        "note": (
            "Excludes fully refunded sales. Partial refunds are not "
            "attributed to individual products."
        ),
        "results": [
            {
                "product_id": r["product_id"],
                "product_name": r["product_name"],
                "sku": r["sku"],
                "currency_code": r["sale__currency_code"],
                "quantity_sold": str(r["quantity_sold"]),
                "revenue": str(r["revenue"] or Decimal("0")),
                "orders": r["orders"],
            }
            for r in rows
        ],
    }


def sales_by_cashier(request) -> dict:
    rows, f, t = _grouped_sales(
        request,
        ["cashier_id", "currency_code"],
        ["cashier__username", "cashier__first_name", "cashier__last_name"],
    )
    return {
        "period": _period(f, t),
        "results": [
            {
                "cashier_id": r["cashier_id"],
                "cashier_name": (
                    f"{r['cashier__first_name']} {r['cashier__last_name']}".strip()
                    or r["cashier__username"]
                ),
                "currency_code": r["currency_code"],
                "total_sales": str(r["total_sales"]),
                "refunds_total": str(r["refunds_total"]),
                "net_sales": str(r["net_sales"]),
                "transactions": r["transactions"],
            }
            for r in rows
        ],
    }


def sales_by_payment_method(request) -> dict:
    """
    REP-009: money collected and refunded per payment method.

    Covers every payment on sales in the period (including part-payments of
    sales not yet fully paid), since this report is about money movements.
    Refunds are reported separately instead of being added to collections.
    """
    base, f, t = _base_sales_qs(request)
    rows = (
        Payment.objects.filter(sale__in=base.values("pk"))
        .values("method", "currency_code")
        .annotate(
            collected=Coalesce(Sum("amount", filter=Q(is_refund=False)), ZERO),
            refunded=Coalesce(Sum("amount", filter=Q(is_refund=True)), ZERO),
            count=Count("id", filter=Q(is_refund=False)),
            refund_count=Count("id", filter=Q(is_refund=True)),
        )
        .order_by("-collected")
    )
    return {
        "period": _period(f, t),
        "results": [
            {
                "method": r["method"],
                "currency_code": r["currency_code"],
                "collected": str(r["collected"]),
                "refunded": str(r["refunded"]),
                "net": str(r["collected"] - r["refunded"]),
                "count": r["count"],
                "refund_count": r["refund_count"],
            }
            for r in rows
        ],
    }
