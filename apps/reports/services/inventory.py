from decimal import Decimal

from django.db.models import F, Sum
from django.utils import timezone

from apps.inventory.models import Inventory, StockMovement

from .common import branch_scope, end_of_day, parse_range, start_of_day


def current_stock(request) -> dict:
    """
    REP-010 + stock valuation: stock by branch, valued at each product's
    latest cost price (INV-013). Costs are left out for roles that may not
    see them.
    """
    from apps.accounts.access import can_see_costs
    from apps.inventory.views import _latest_cost

    qs = Inventory.objects.filter(
        branch__business_id=request.user.business_id
    ).select_related("branch", "product").annotate(current_cost=_latest_cost())

    scoped = branch_scope(request)
    if scoped is not None:
        qs = qs.filter(branch_id__in=scoped)

    show_costs = can_see_costs(request.user)
    results = []
    for inv in qs.order_by("branch__name", "product__name"):
        row = {
            "branch_id": inv.branch_id,
            "branch_name": inv.branch.name,
            "product_id": inv.product_id,
            "product_name": inv.product.name,
            "sku": inv.product.sku,
            "unit": inv.product.unit,
            "quantity": str(inv.quantity),
            "minimum_threshold": str(inv.minimum_threshold),
            "is_low_stock": inv.is_low_stock,
        }
        if show_costs:
            cost = inv.current_cost
            row["unit_cost"] = None if cost is None else str(cost)
            row["stock_value"] = None if cost is None else str(cost * inv.quantity)
        results.append(row)
    return {"generated_at": timezone.now().isoformat(), "results": results}


def low_stock(request) -> dict:
    qs = (
        Inventory.objects
        .filter(branch__business_id=request.user.business_id)
        .filter(quantity__lte=F("minimum_threshold"))
        .select_related("branch", "product")
    )
    scoped = branch_scope(request)
    if scoped is not None:
        qs = qs.filter(branch_id__in=scoped)

    return {
        "generated_at": timezone.now().isoformat(),
        "results": [
            {
                "branch_id": inv.branch_id,
                "branch_name": inv.branch.name,
                "product_id": inv.product_id,
                "product_name": inv.product.name,
                "sku": inv.product.sku,
                "unit": inv.product.unit,
                "quantity": str(inv.quantity),
                "minimum_threshold": str(inv.minimum_threshold),
            }
            for inv in qs.order_by("branch__name", "product__name")
        ],
    }


def stock_movement_report(request) -> dict:
    f, t = parse_range(request)
    qs = StockMovement.objects.filter(
        branch__business_id=request.user.business_id,
        created_at__gte=start_of_day(f),
        created_at__lte=end_of_day(t),
    ).select_related("branch", "product", "performed_by")

    scoped = branch_scope(request)
    if scoped is not None:
        qs = qs.filter(branch_id__in=scoped)

    params = request.query_params
    if params.get("branch"):
        qs = qs.filter(branch_id=params["branch"])
    if params.get("product"):
        qs = qs.filter(product_id=params["product"])
    if params.get("type"):
        # One or several types: "damaged,expired" = losses; "purchase" = purchasing (REP-012).
        types = [x.strip() for x in params["type"].split(",") if x.strip()]
        qs = qs.filter(movement_type__in=types)

    return {
        "period": {"from": f.isoformat(), "to": t.isoformat()},
        "results": [
            {
                "id": mv.id,
                "branch_name": mv.branch.name,
                "product_name": mv.product.name,
                "sku": mv.product.sku,
                "unit": mv.product.unit,
                "movement_type": mv.movement_type,
                "delta": str(mv.delta),
                "quantity_after": str(mv.quantity_after),
                "reference": mv.reference,
                "reason": mv.reason,
                "performed_by": mv.performed_by.get_full_name() or mv.performed_by.username,
                "created_at": mv.created_at.isoformat(),
            }
            for mv in qs.order_by("-created_at")
        ],
    }