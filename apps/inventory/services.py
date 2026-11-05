from decimal import Decimal

from django.db import transaction
from rest_framework.exceptions import ValidationError

from apps.audit.services import log_action
from apps.branches.models import Branch
from apps.products.models import Product

from .models import Inventory, StockMovement, StockMovementType


# ------------------------------------------------------------------------------
# Internal helper
# ------------------------------------------------------------------------------
def _ensure_inventory_row(branch: Branch, product: Product) -> Inventory:
    """
    Get or create the Inventory row, atomically.
    Uses select_for_update on existing rows to serialize concurrent access.
    """
    inv = (
        Inventory.objects
        .select_for_update()
        .filter(branch=branch, product=product)
        .first()
    )
    if inv is None:
        inv = Inventory.objects.create(
            branch=branch, product=product, quantity=Decimal("0"),
        )
    return inv


def _apply_movement(
    *,
    branch: Branch,
    product: Product,
    movement_type: str,
    delta: Decimal,
    performed_by,
    reason: str = "",
    reference: str = "",
    sale=None,
    allow_negative: bool = False,
) -> StockMovement:
    """
    Apply a signed delta to the inventory and record the movement.
    Must be called inside a transaction (or wraps itself).
    """
    if delta == 0:
        raise ValidationError("Delta must be non-zero.")

    inv = _ensure_inventory_row(branch, product)
    new_qty = inv.quantity + delta

    if new_qty < 0 and not allow_negative:
        raise ValidationError(
            f"Insufficient stock for '{product.name}' at {branch.name}. "
            f"Current: {inv.quantity}, requested delta: {delta}."
        )

    inv.quantity = new_qty
    inv.save(update_fields=["quantity", "updated_at"])

    if inv.quantity <= inv.minimum_threshold and movement_type != StockMovementType.PURCHASE:
        from apps.notifications.services import notify_low_stock
        try:
            notify_low_stock(
                branch=branch,
                product=product,
                current_quantity=inv.quantity,
                threshold=inv.minimum_threshold,
            )
        except Exception:
            pass  # never fail a stock movement because of a notification

    movement = StockMovement.objects.create(
        branch=branch,
        product=product,
        movement_type=movement_type,
        delta=delta,
        quantity_after=new_qty,
        reason=reason,
        reference=reference,
        sale=sale,
        performed_by=performed_by,
    )

    log_action(
        business=branch.business,
        branch=branch,
        user=performed_by,
        action="stock_movement",
        target_type="Inventory",
        target_id=f"{branch.pk}:{product.pk}",
        metadata={
            "movement_type": movement_type,
            "delta": str(delta),
            "quantity_after": str(new_qty),
            "reference": reference,
            "sale_id": str(sale.pk) if sale else None,
        },
    )

    return movement


# ------------------------------------------------------------------------------
# Public operations
# ------------------------------------------------------------------------------
@transaction.atomic
def record_inflow(
    *,
    branch: Branch,
    product: Product,
    quantity: Decimal,
    performed_by,
    movement_type: str = StockMovementType.PURCHASE,
    reason: str = "",
    reference: str = "",
) -> StockMovement:
    """Record incoming stock (purchase, return, transfer-in)."""
    if quantity <= 0:
        raise ValidationError("Quantity must be positive.")
    if movement_type not in (
        StockMovementType.PURCHASE,
        StockMovementType.RETURN,
        StockMovementType.TRANSFER_IN,
        StockMovementType.ADJUSTMENT,
    ):
        raise ValidationError(f"'{movement_type}' is not an inflow type.")
    return _apply_movement(
        branch=branch, product=product, movement_type=movement_type,
        delta=quantity, performed_by=performed_by, reason=reason,
        reference=reference,
    )


@transaction.atomic
def record_outflow(
    *,
    branch: Branch,
    product: Product,
    quantity: Decimal,
    performed_by,
    movement_type: str = StockMovementType.ADJUSTMENT,
    reason: str = "",
    reference: str = "",
    allow_negative: bool = False,
) -> StockMovement:
    """
    Record outgoing stock.
    Manual adjustments MUST include a reason (BRULE-009).
    """
    if quantity <= 0:
        raise ValidationError("Quantity must be positive.")
    if movement_type in (
        StockMovementType.ADJUSTMENT,
        StockMovementType.DAMAGED,
        StockMovementType.EXPIRED,
    ) and not reason.strip():
        raise ValidationError("A reason is required for this movement type.")
    if movement_type not in (
        StockMovementType.ADJUSTMENT,
        StockMovementType.DAMAGED,
        StockMovementType.EXPIRED,
        StockMovementType.TRANSFER_OUT,
        StockMovementType.SALE,
    ):
        raise ValidationError(f"'{movement_type}' is not an outflow type.")
    return _apply_movement(
        branch=branch, product=product, movement_type=movement_type,
        delta=-quantity, performed_by=performed_by, reason=reason,
        reference=reference, allow_negative=allow_negative,
    )


@transaction.atomic
def manual_adjustment(
    *,
    branch: Branch,
    product: Product,
    new_quantity: Decimal,
    reason: str,
    performed_by,
) -> StockMovement:
    """
    Set inventory to an exact value, recording the delta.
    Used for physical stock counts (INV-017, INV-018).
    """
    if not reason.strip():
        raise ValidationError("A reason is required for manual adjustments.")
    if new_quantity < 0:
        raise ValidationError("New quantity cannot be negative.")

    inv = _ensure_inventory_row(branch, product)
    delta = new_quantity - inv.quantity
    if delta == 0:
        raise ValidationError("New quantity equals current quantity.")

    return _apply_movement(
        branch=branch, product=product,
        movement_type=StockMovementType.ADJUSTMENT,
        delta=delta, performed_by=performed_by, reason=reason,
        allow_negative=False,
    )


@transaction.atomic
def consume_for_sale(
    *,
    sale,
    performed_by,
) -> list[StockMovement]:
    """
    Deduct stock for each SaleItem where the product is tracked in inventory
    at the sale's branch. Only applies to products with an existing
    Inventory row (i.e., the business chooses which products to track).

    Called from the POS right after a sale is created (POS-023, INV-005).
    """
    movements = []
    for item in sale.items.all():
        inv_exists = Inventory.objects.filter(
            branch=sale.branch, product=item.product
        ).exists()
        if not inv_exists:
            continue
        movements.append(
            _apply_movement(
                branch=sale.branch,
                product=item.product,
                movement_type=StockMovementType.SALE,
                delta=-item.quantity,
                performed_by=performed_by,
                reference=sale.transaction_number,
                sale=sale,
            )
        )
    return movements