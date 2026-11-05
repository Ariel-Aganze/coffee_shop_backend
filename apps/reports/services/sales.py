from decimal import Decimal

from django.db.models import Count, DecimalField, F, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.payments.models import Payment
from apps.sales.models import Sale, SaleItem, SaleStatus

from .common import branch_scope, end_of_day, parse_range, start_of_day


ZERO = Value(Decimal("0"), output_field=DecimalField(max_digits=18, decimal_places=4))


def _base_sales_qs(request, *, currency_code=None):
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
    if currency_code:
        qs = qs.filter(currency_code=currency_code)
    return qs, f, t


def sales_summary(request) -> dict:
    """
    REP-001..REP-004, REP-005, REP-006:
    Total sales, transaction count, average ticket.
    Grouped by currency so a currency change never mixes amounts.
    """
    qs, f, t = _base_sales_qs(request)

    rows = (
        qs.values("currency_code", "currency_decimal_places")
        .annotate(
            total_sales=Coalesce(Sum("total_amount"), ZERO),
            transactions=Count("id"),
            refunds_total=Coalesce(
                Sum(
                    "payments__amount",
                    filter=Q(payments__is_refund=True),
                ),
                ZERO,
            ),
        )
        .order_by("currency_code")
    )

    results = []
    for row in rows:
        tx = row["transactions"] or 0
        total = row["total_sales"] or Decimal("0")
        avg = (total / tx) if tx else Decimal("0")
        results.append({
            "currency_code": row["currency_code"],
            "currency_decimal_places": row["currency_decimal_places"],
            "total_sales": str(total),
            "refunds_total": str(row["refunds_total"] or Decimal("0")),
            "transactions": tx,
            "average_transaction_value": str(avg),
        })
    return {"period": {"from": f.isoformat(), "to": t.isoformat()}, "results": results}


def sales_by_branch(request) -> dict:
    qs, f, t = _base_sales_qs(request)
    rows = (
        qs.values("branch_id", "branch__name", "currency_code", "currency_decimal_places")
        .annotate(
            total_sales=Coalesce(Sum("total_amount"), ZERO),
            transactions=Count("id"),
        )
        .order_by("-total_sales")
    )
    return {
        "period": {"from": f.isoformat(), "to": t.isoformat()},
        "results": [
            {
                "branch_id": r["branch_id"],
                "branch_name": r["branch__name"],
                "currency_code": r["currency_code"],
                "total_sales": str(r["total_sales"] or Decimal("0")),
                "transactions": r["transactions"],
            }
            for r in rows
        ],
    }


def sales_by_product(request) -> dict:
    qs, f, t = _base_sales_qs(request)
    rows = (
        SaleItem.objects.filter(
            sale__in=qs,
        )
        .values(
            "product_id", "product_name", "sku",
            "sale__currency_code", "sale__currency_decimal_places",
        )
        .annotate(
            quantity_sold=Coalesce(Sum("quantity"), Value(Decimal("0"),
                output_field=DecimalField(max_digits=18, decimal_places=4))),
            revenue=Coalesce(Sum("line_total"), ZERO),
            orders=Count("sale_id", distinct=True),
        )
        .order_by("-revenue")
    )
    return {
        "period": {"from": f.isoformat(), "to": t.isoformat()},
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
    qs, f, t = _base_sales_qs(request)
    rows = (
        qs.values("cashier_id", "cashier__username", "cashier__first_name",
                  "cashier__last_name", "currency_code", "currency_decimal_places")
        .annotate(
            total_sales=Coalesce(Sum("total_amount"), ZERO),
            transactions=Count("id"),
        )
        .order_by("-total_sales")
    )
    return {
        "period": {"from": f.isoformat(), "to": t.isoformat()},
        "results": [
            {
                "cashier_id": r["cashier_id"],
                "cashier_name": (
                    f"{r['cashier__first_name']} {r['cashier__last_name']}".strip()
                    or r["cashier__username"]
                ),
                "currency_code": r["currency_code"],
                "total_sales": str(r["total_sales"] or Decimal("0")),
                "transactions": r["transactions"],
            }
            for r in rows
        ],
    }


def sales_by_payment_method(request) -> dict:
    qs, f, t = _base_sales_qs(request)
    rows = (
        Payment.objects.filter(sale__in=qs)
        .values("method", "currency_code")
        .annotate(total=Coalesce(Sum("amount"), ZERO), count=Count("id"))
        .order_by("-total")
    )
    return {
        "period": {"from": f.isoformat(), "to": t.isoformat()},
        "results": [
            {
                "method": r["method"],
                "currency_code": r["currency_code"],
                "total": str(r["total"] or Decimal("0")),
                "count": r["count"],
            }
            for r in rows
        ],
    }