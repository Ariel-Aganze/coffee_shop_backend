from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.db.models import DecimalField, F, OuterRef, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from rest_framework.exceptions import ValidationError


def parse_date(value: str | None, field_name: str = "date") -> date | None:
    if value is None or value == "":
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        raise ValidationError(f"Invalid {field_name} format. Use YYYY-MM-DD.")


def parse_range(request) -> tuple[date, date]:
    """
    Returns (from_date, to_date). If neither is provided, defaults to today.
    If only `from` is provided, `to` defaults to today.
    If only `to` is provided, `from` defaults to 30 days before `to`.
    """
    today = timezone.localdate()
    f = parse_date(request.query_params.get("from"), "from")
    t = parse_date(request.query_params.get("to"), "to")

    if f is None and t is None:
        f = t = today
    elif f is None:
        f = t - timedelta(days=30)
    elif t is None:
        t = today

    if f > t:
        raise ValidationError("`from` must be on or before `to`.")
    return f, t


def start_of_day(d: date) -> datetime:
    return timezone.make_aware(datetime.combine(d, time.min))


def end_of_day(d: date) -> datetime:
    return timezone.make_aware(datetime.combine(d, time.max))


MONEY = DecimalField(max_digits=18, decimal_places=4)
ZERO = Value(Decimal("0"), output_field=MONEY)


def _paid_subquery():
    from apps.payments.models import Payment

    return Coalesce(
        Subquery(
            Payment.objects.filter(sale=OuterRef("pk"), is_refund=False)
            .values("sale")
            .annotate(s=Sum("amount"))
            .values("s")[:1],
            output_field=MONEY,
        ),
        ZERO,
    )


def recognized_sales(sales_qs):
    """
    Sales that count as revenue: fully paid ones.

    A sale is created first and paid in a second request, so an unpaid sale
    is an incomplete transaction (the customer never paid) and must not be
    reported as revenue. The paid amount is computed with a correlated
    subquery so the outer query never JOINs payments — joining would
    multiply every sale by its number of payments.
    """
    return (
        sales_qs.annotate(paid_total=_paid_subquery())
        .filter(paid_total__gte=F("total_amount"))
    )


def unpaid_sales(sales_qs):
    """Sales not (fully) paid yet — reported separately, never as revenue."""
    return (
        sales_qs.annotate(paid_total=_paid_subquery())
        .filter(paid_total__lt=F("total_amount"))
    )


def refunds_by(sales_qs, *keys: str) -> dict:
    """
    Refunded amounts for the given sales, grouped by Sale fields `keys`
    (e.g. "currency_code", "branch_id"). Returns {tuple(key values): Decimal}.

    Queried from Payment on its own so sale totals are never fanned out.
    Refunds are attributed to the sale's date, not the refund's date.
    """
    from apps.payments.models import Payment

    sale_keys = [f"sale__{k}" for k in keys]
    rows = (
        Payment.objects.filter(is_refund=True, sale__in=sales_qs.values("pk"))
        .values(*sale_keys)
        .annotate(total=Sum("amount"))
        .order_by()
    )
    return {
        tuple(r[k] for k in sale_keys): r["total"] or Decimal("0")
        for r in rows
    }


def branch_scope(request):
    """
    Branch ids this report covers, or None for every branch of the business.
    Applies the role rules (apps.accounts.access) and the optional
    `?branch=<id>` filter (DASH-012).
    """
    from apps.accounts.access import requested_branch_scope

    return requested_branch_scope(request)