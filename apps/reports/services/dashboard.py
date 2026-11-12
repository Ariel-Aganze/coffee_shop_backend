"""
Owner dashboard (SRS 5.3, DASH-001..DASH-014, 10.1).

One endpoint returning everything the dashboard screen needs, scoped to the
user's branches and the optional `?branch=` filter, for a date range
(`?from=&to=`, default: month to date). Day boundaries use the business
timezone (activated by the report view).

Money figures are for the business's CURRENT primary currency only, so
amounts in different currencies are never added together (10.3). If the
period also contains sales in another currency (after a currency change),
`other_currencies_in_period` lists them so the UI can point the user to the
per-currency reports.

Revenue follows the same rules as the reports: fully paid sales only,
refunds netted out, and revenue / gross profit / operating result kept
distinct (5.3, BRULE-015).
"""
from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, OuterRef, Subquery, Sum
from django.db.models.functions import Coalesce, TruncDate
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.branches.models import Branch
from apps.cash_management.models import CashSession, CashSessionStatus
from apps.cash_management.services import compute_expected_cash
from apps.expenses.models import Expense, ExpenseStatus
from apps.inventory.models import Inventory, StockMovement
from apps.notifications.models import Notification
from apps.payments.models import Payment
from apps.products.models import Product, ProductPrice
from apps.sales.models import Sale, SaleItem, SaleStatus
from apps.settings.models import BusinessSettings

from .common import (
    MONEY,
    ZERO,
    branch_scope,
    end_of_day,
    parse_date,
    recognized_sales,
    refunds_by,
    start_of_day,
)
from .financial import _cogs_for_currency

TOP_N = 5
LIST_N = 10
MAX_TREND_DAYS = 366
# Audit actions too noisy to be "business activity" on a dashboard.
QUIET_ACTIONS = ("user_logged_in", "user_logged_out", "password_reset_requested")


# ------------------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------------------
def _money(value) -> str:
    return str(value if value is not None else Decimal("0"))


def _scoped(qs, scope, field="branch_id"):
    return qs if scope is None else qs.filter(**{f"{field}__in": scope})


def _period(request):
    """Dashboard default period is month-to-date (not just today)."""
    today = timezone.localdate()
    f = parse_date(request.query_params.get("from"), "from")
    t = parse_date(request.query_params.get("to"), "to")
    t = t or today
    f = f or t.replace(day=1)
    if f > t:
        from rest_framework.exceptions import ValidationError
        raise ValidationError("`from` must be on or before `to`.")
    return f, t


def _sales_qs(user, scope, currency, f, t):
    qs = Sale.objects.filter(
        business_id=user.business_id,
        status__in=[SaleStatus.COMPLETED, SaleStatus.REFUNDED],
        currency_code=currency,
        created_at__gte=start_of_day(f),
        created_at__lte=end_of_day(t),
    )
    return _scoped(qs, scope)


def _sales_figures(paid_qs, currency) -> dict:
    """Gross / refunds / net / count / average for already-recognized sales."""
    agg = paid_qs.aggregate(gross=Coalesce(Sum("total_amount"), ZERO), tx=Count("id"))
    gross = agg["gross"] or Decimal("0")
    refunded = refunds_by(paid_qs, "currency_code").get((currency,), Decimal("0"))
    net = gross - refunded
    tx = agg["tx"] or 0
    return {
        "gross_sales": _money(gross),
        "refunds": _money(refunded),
        "net_sales": _money(net),
        "transactions": tx,
        "average_transaction_value": _money(net / tx if tx else Decimal("0")),
    }


def _expenses_qs(user, scope, currency, f, t, status=ExpenseStatus.APPROVED):
    qs = Expense.objects.filter(
        business_id=user.business_id, currency_code=currency, status=status,
        expense_date__gte=f, expense_date__lte=t,
    )
    # Central (branch-less) expenses only belong in a business-wide view.
    return qs if scope is None else qs.filter(branch_id__in=scope)


# ------------------------------------------------------------------------------
# Sections
# ------------------------------------------------------------------------------
def _headline(user, scope, currency):
    """DASH-001, DASH-002: today, this week (Mon–today), this month."""
    today = timezone.localdate()
    ranges = {
        "today": (today, today),
        "this_week": (today - timedelta(days=today.weekday()), today),
        "this_month": (today.replace(day=1), today),
    }
    out = {}
    for key, (f, t) in ranges.items():
        paid = recognized_sales(_sales_qs(user, scope, currency, f, t))
        out[key] = {"from": f.isoformat(), "to": t.isoformat(),
                    **_sales_figures(paid, currency)}
    return out


def _overview(user, scope, currency, f, t, paid):
    """DASH-003..DASH-005 + financial overview: revenue ≠ gross profit ≠ result."""
    figures = _sales_figures(paid, currency)
    revenue = Decimal(figures["net_sales"])
    cogs = _cogs_for_currency(paid, currency)
    expenses = (
        _expenses_qs(user, scope, currency, f, t)
        .aggregate(s=Coalesce(Sum("amount"), ZERO))["s"]
    )
    gross_profit = revenue - cogs
    return {
        **figures,
        "revenue": _money(revenue),
        "cost_of_goods_sold": _money(cogs),
        "gross_profit": _money(gross_profit),
        "expenses": _money(expenses),
        "operating_result": _money(gross_profit - expenses),
        "notes": [
            "Revenue = fully paid sales minus refunds.",
            "Cost of goods sold uses each product's current cost price.",
            "Operating result = gross profit − approved expenses. It is not "
            "net profit (taxes, depreciation, etc. are not included).",
        ],
    }


def _branch_performance(user, scope, currency, f, t, paid):
    """DASH-006, DASH-007, branch comparison (10.2)."""
    branches = _scoped(
        Branch.objects.filter(business_id=user.business_id, is_active=True),
        scope, field="id",
    ).order_by("name")

    sales = {
        r["branch_id"]: r
        for r in paid.values("branch_id").annotate(
            gross=Coalesce(Sum("total_amount"), ZERO), tx=Count("id"),
        ).order_by()
    }
    refunds = refunds_by(paid, "branch_id")
    expenses = {
        r["branch_id"]: r["s"]
        for r in _expenses_qs(user, scope, currency, f, t)
        .values("branch_id").annotate(s=Sum("amount")).order_by()
    }

    rows = []
    for b in branches:
        s = sales.get(b.id, {})
        gross = s.get("gross") or Decimal("0")
        tx = s.get("tx") or 0
        net = gross - refunds.get((b.id,), Decimal("0"))
        rows.append({
            "branch_id": b.id,
            "branch_name": b.name,
            "net_sales": net,
            "transactions": tx,
            "average_transaction_value": net / tx if tx else Decimal("0"),
            "expenses": expenses.get(b.id) or Decimal("0"),
        })
    rows.sort(key=lambda r: r["net_sales"], reverse=True)

    def fmt(r):
        return {**r, **{k: _money(r[k]) for k in
                        ("net_sales", "average_transaction_value", "expenses")}}

    comparable = len(rows) >= 2
    return {
        "branches": [fmt(r) for r in rows],
        # Only meaningful when there is something to compare.
        "best_branch": fmt(rows[0]) if comparable else None,
        "lowest_branch": fmt(rows[-1]) if comparable else None,
    }


def _sales_trend(paid, f, t):
    """Daily net sales for the period (branch sales trends)."""
    if (t - f).days + 1 > MAX_TREND_DAYS:
        return {"truncated": True, "days": []}
    by_day = {
        r["day"]: r
        for r in paid.annotate(day=TruncDate("created_at")).values("day").annotate(
            gross=Coalesce(Sum("total_amount"), ZERO), tx=Count("id"),
        ).order_by()
    }
    refunds_by_day = {
        r["day"]: r["s"]
        for r in Payment.objects.filter(is_refund=True, sale__in=paid.values("pk"))
        .annotate(day=TruncDate("sale__created_at"))
        .values("day").annotate(s=Sum("amount")).order_by()
    }
    days = []
    day = f
    while day <= t:
        row = by_day.get(day, {})
        gross = row.get("gross") or Decimal("0")
        days.append({
            "date": day.isoformat(),
            "net_sales": _money(gross - (refunds_by_day.get(day) or Decimal("0"))),
            "transactions": row.get("tx") or 0,
        })
        day += timedelta(days=1)
    return {"truncated": False, "days": days}


def _product_performance(user, paid):
    """DASH-008 + best/lowest-selling products and product revenue."""
    sold = {
        r["product_id"]: r
        for r in SaleItem.objects.filter(
            sale__in=paid.exclude(status=SaleStatus.REFUNDED).values("pk"),
        ).values("product_id").annotate(
            qty=Coalesce(Sum("quantity"), ZERO), revenue=Coalesce(Sum("line_total"), ZERO),
        ).order_by()
    }
    products = Product.objects.filter(business_id=user.business_id, is_active=True)
    rows = [
        {
            "product_id": p.id, "product_name": p.name, "sku": p.sku,
            "quantity_sold": sold.get(p.id, {}).get("qty") or Decimal("0"),
            "revenue": sold.get(p.id, {}).get("revenue") or Decimal("0"),
        }
        for p in products
    ]

    def fmt(r):
        return {**r, "quantity_sold": str(r["quantity_sold"]), "revenue": _money(r["revenue"])}

    best = sorted((r for r in rows if r["quantity_sold"] > 0),
                  key=lambda r: (-r["quantity_sold"], -r["revenue"]))[:TOP_N]
    # Lowest sellers include active products that sold nothing at all.
    lowest = sorted(rows, key=lambda r: (r["quantity_sold"], r["revenue"],
                                         r["product_name"]))[:TOP_N]
    return {"best_selling": [fmt(r) for r in best],
            "lowest_selling": [fmt(r) for r in lowest]}


def _inventory(user, scope):
    """DASH-009 + stock valuation and recent movements."""
    now = timezone.now()
    latest_cost = Subquery(
        ProductPrice.objects.filter(product=OuterRef("product"), effective_from__lte=now)
        .order_by("-effective_from").values("cost_price")[:1],
        output_field=MONEY,
    )
    stock = _scoped(
        Inventory.objects.filter(branch__business_id=user.business_id), scope,
    ).select_related("branch", "product").annotate(unit_cost=latest_cost)

    valuation = Decimal("0")
    without_cost = 0
    low = []
    for inv in stock:
        if inv.unit_cost:
            valuation += inv.quantity * inv.unit_cost
        else:
            without_cost += 1
        if inv.quantity <= inv.minimum_threshold:
            low.append(inv)
    low.sort(key=lambda i: i.quantity - i.minimum_threshold)

    movements = _scoped(
        StockMovement.objects.filter(branch__business_id=user.business_id), scope,
    ).select_related("branch", "product", "performed_by").order_by("-created_at")[:TOP_N]

    return {
        "low_stock_count": len(low),
        "low_stock": [
            {
                "branch_id": i.branch_id, "branch_name": i.branch.name,
                "product_id": i.product_id, "product_name": i.product.name,
                "quantity": str(i.quantity), "minimum_threshold": str(i.minimum_threshold),
            }
            for i in low[:LIST_N]
        ],
        "stock_valuation": _money(valuation),
        "stock_valuation_method": "Quantity on hand × current cost price.",
        "items_without_cost_price": without_cost,
        "recent_movements": [
            {
                "branch_name": m.branch.name, "product_name": m.product.name,
                "movement_type": m.movement_type, "delta": str(m.delta),
                "performed_by": m.performed_by.username,
                "created_at": m.created_at.isoformat(),
            }
            for m in movements
        ],
    }


def _operations(user, scope):
    """Active cash sessions, recent transactions, approvals, alerts, activity."""
    sessions = _scoped(
        CashSession.objects.filter(branch__business_id=user.business_id,
                                   status=CashSessionStatus.OPEN),
        scope,
    ).select_related("branch", "cashier").order_by("opened_at")

    recent_sales = _scoped(
        Sale.objects.filter(business_id=user.business_id), scope,
    ).select_related("branch", "cashier").order_by("-created_at")[:LIST_N]

    pending = Expense.objects.filter(
        business_id=user.business_id, status=ExpenseStatus.PENDING,
    )
    if scope is not None:
        pending = pending.filter(branch_id__in=scope)
    pending_rows = pending.values("currency_code").annotate(
        count=Count("id"), total=Coalesce(Sum("amount"), ZERO),
    ).order_by()

    activity = AuditLog.objects.filter(business_id=user.business_id).exclude(
        action__in=QUIET_ACTIONS,
    )
    if scope is not None:
        activity = activity.filter(branch_id__in=scope)
    activity = activity.select_related("user", "branch").order_by("-created_at")[:LIST_N]

    return {
        "active_cash_sessions": [
            {
                "session_id": s.id, "branch_name": s.branch.name,
                "cashier": s.cashier.get_full_name() or s.cashier.username,
                "opened_at": s.opened_at.isoformat(),
                "currency_code": s.currency_code,
                "expected_cash": _money(compute_expected_cash(s)["expected_cash"]),
            }
            for s in sessions
        ],
        "recent_transactions": [
            {
                "sale_id": s.id, "transaction_number": s.transaction_number,
                "branch_name": s.branch.name,
                "cashier": s.cashier.get_full_name() or s.cashier.username,
                "total_amount": _money(s.total_amount), "currency_code": s.currency_code,
                "status": s.status, "created_at": s.created_at.isoformat(),
            }
            for s in recent_sales
        ],
        "pending_approvals": {
            "expenses": [
                {"currency_code": r["currency_code"], "count": r["count"],
                 "total": _money(r["total"])}
                for r in pending_rows
            ],
        },
        "unread_notifications": Notification.objects.filter(
            recipient=user, is_read=False,
        ).count(),
        "recent_activity": [
            {
                "action": a.action,
                "user": (a.user.get_full_name() or a.user.username) if a.user else None,
                "branch_name": a.branch.name if a.branch else None,
                "target_type": a.target_type, "target_id": a.target_id,
                "created_at": a.created_at.isoformat(),
            }
            for a in activity
        ],
    }


# ------------------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------------------
def dashboard(request) -> dict:
    user = request.user
    scope = branch_scope(request)
    f, t = _period(request)

    currency = BusinessSettings.objects.select_related("primary_currency").get(
        business_id=user.business_id,
    ).primary_currency

    paid = recognized_sales(_sales_qs(user, scope, currency.code, f, t))

    other_currencies = sorted(set(
        _scoped(Sale.objects.filter(
            business_id=user.business_id,
            created_at__gte=start_of_day(f), created_at__lte=end_of_day(t),
        ).exclude(currency_code=currency.code), scope)
        .values_list("currency_code", flat=True).distinct()
    ))

    return {
        "currency": {
            "code": currency.code, "symbol": currency.symbol,
            "decimal_places": currency.decimal_places,
        },
        "other_currencies_in_period": other_currencies,
        "period": {"from": f.isoformat(), "to": t.isoformat()},
        "scope": {
            "branch_ids": scope,  # null = all branches of the business
            "timezone": timezone.get_current_timezone_name(),
        },
        "sales": _headline(user, scope, currency.code),
        "overview": _overview(user, scope, currency.code, f, t, paid),
        "branch_performance": _branch_performance(user, scope, currency.code, f, t, paid),
        "sales_trend": _sales_trend(paid, f, t),
        "products": _product_performance(user, paid),
        "inventory": _inventory(user, scope),
        "operations": _operations(user, scope),
    }
