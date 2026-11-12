"""
Current-price lookups done in SQL, so product lists don't run two extra
queries per product. A product's price at a moment is the ProductPrice row
with the latest `effective_from <= that moment` (see ProductPrice).
"""
from django.db.models import DecimalField, OuterRef, Subquery
from django.utils import timezone

from .models import ProductPrice

_PRICE = DecimalField(max_digits=14, decimal_places=4)


def _latest(field: str, at):
    return Subquery(
        ProductPrice.objects.filter(product=OuterRef("pk"), effective_from__lte=at)
        .order_by("-effective_from", "-id")
        .values(field)[:1],
        output_field=_PRICE,
    )


def with_current_prices(product_qs, at=None):
    """Annotate `current_selling_price` and `current_cost_price` (None if unpriced)."""
    at = at or timezone.now()
    return product_qs.annotate(
        current_selling_price=_latest("selling_price", at),
        current_cost_price=_latest("cost_price", at),
    )
