from decimal import Decimal

from django.db.models import F, Sum
from django.utils import timezone

from apps.inventory.models import Inventory, StockMovement

from .common import branch_scope, end_of_day, parse_range, start_of_day


def current_stock(request) -> dict:
    """
    REP-010: snapshot of stock by branch.
    """
    qs = Inventory.objects.filter(
        branch__business_id=request.user.business_id
    ).select_related("branch", "product")

    scoped = branch_scope(request)
    if scoped is not None:
        qs = qs.filter(branch_id__in=scoped)

    branch_id = request.query_params.get("branch")
    if branch_id:
        qs = qs.filter(branch_id=branch_id)

    return {
        "generated_at": timezone.now().isoformat(),
        "results": [
            {
                "branch_id": inv.branch_id,
                "branch_name": inv.branch.name,
                "product_id": inv.product_id,
                "product_name": inv.product.name,
                "sku": inv.product.sku,
                "quantity": str(inv.quantity),
                "minimum_threshold": str(inv.minimum_threshold),
                "is_low_stock": inv.is_low_stock,
            }
            for inv in qs.order_by("branch__name", "product__name")
        ],
    }


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
        qs = qs.filter(movement_type=params["type"])

    return {
        "period": {"from": f.isoformat(), "to": t.isoformat()},
        "results": [
            {
                "id": mv.id,
                "branch_name": mv.branch.name,
                "product_name": mv.product.name,
                "sku": mv.product.sku,
                "movement_type": mv.movement_type,
                "delta": str(mv.delta),
                "quantity_after": str(mv.quantity_after),
                "reference": mv.reference,
                "reason": mv.reason,
                "performed_by": mv.performed_by.username,
                "created_at": mv.created_at.isoformat(),
            }
            for mv in qs.order_by("-created_at")
        ],
    }