import hashlib
import json
import uuid
from datetime import datetime, timezone as dt_timezone
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.audit.services import log_action
from apps.branches.models import Branch
from apps.products.models import (
    BranchProductAvailability,
    Product,
    ProductPrice,
)
from apps.settings.models import BusinessSettings

from .models import (
    IdempotencyRecord,
    Sale,
    SaleCounter,
    SaleItem,
    SaleStatus,
)


# ------------------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------------------
def _round(value: Decimal, places: int) -> Decimal:
    """
    Round monetary values using ROUND_HALF_UP (banker's rounding is NOT what
    retail usually wants). Uses the currency precision from the settings.
    """
    quant = Decimal(1).scaleb(-places)  # e.g. places=2 → 0.01
    return value.quantize(quant, rounding=ROUND_HALF_UP)


def _hash_payload(payload) -> str:
    """Deterministic hash of the request payload for idempotency."""
    if isinstance(payload, dict):
        payload = json.dumps(payload, sort_keys=True, default=str)
    elif not isinstance(payload, (bytes, str)):
        payload = str(payload)
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _reserve_next_sequence(branch: Branch, when: datetime) -> int:
    """Atomically reserve a sequence number for this branch/day."""
    day = when.date()
    # Ensure a row exists without creating a race — use get_or_create inside
    # the outer transaction; if the row doesn't exist, creating it is fine.
    counter, _ = SaleCounter.objects.select_for_update().get_or_create(
        branch=branch, date=day, defaults={"last_value": 0},
    )
    counter.last_value += 1
    counter.save(update_fields=["last_value"])
    return counter.last_value


def _build_transaction_number(branch: Branch, sequence: int, when: datetime) -> str:
    day = when.strftime("%Y%m%d")
    # Use the first 4 chars of the branch name, uppercased, cleaned.
    branch_slug = "".join(c for c in branch.name.upper() if c.isalnum())[:4] or "BRCH"
    return f"TXN-{branch_slug}-{day}-{sequence:06d}"


def _validate_branch_access(user, branch: Branch):
    if branch.business_id != user.business_id:
        raise ValidationError("Branch does not belong to your business.")
    if user.role not in ("owner", "branch_manager", "cashier"):
        raise ValidationError("Your role is not allowed to create sales.")
    if user.role != "owner" and branch.id not in user.assigned_branch_ids:
        raise ValidationError("You are not assigned to this branch.")


def _validate_product_available(branch: Branch, product: Product):
    if not product.is_active:
        raise ValidationError(f"Product '{product.name}' is not active.")
    if product.business_id != branch.business_id:
        raise ValidationError("Product does not belong to this business.")
    if product.category_id is not None and not product.category.is_active:
        raise ValidationError(f"Category of '{product.name}' is not active.")

    # Per-branch availability: a missing row means "available by default".
    avail = (
        BranchProductAvailability.objects
        .filter(branch=branch, product=product)
        .first()
    )
    if avail and not avail.is_available:
        raise ValidationError(
            f"Product '{product.name}' is not available at this branch."
        )


# ------------------------------------------------------------------------------
# Main entry point
# ------------------------------------------------------------------------------
@transaction.atomic
def create_sale(
    *,
    user,
    branch: Branch,
    items_payload: list[dict],
    idempotency_key: str | None = None,
    request_payload_for_hash: dict | None = None,
    note: str = "",
) -> tuple[Sale, bool]:
    """
    Create a sale atomically.

    Args:
        user: authenticated user creating the sale
        branch: Branch instance (already validated as accessible)
        items_payload: list of {"product_id": int, "quantity": "1.0000",
                                "discount_amount": "0.0000"}
        idempotency_key: optional client-supplied unique key
        request_payload_for_hash: the raw request dict, for idempotency
        note: optional free text

    Returns:
        (sale, replayed) where replayed=True if the response is a replay of
        a previous request (idempotency).
    """
    _validate_branch_access(user, branch)

    # ---------------- Idempotency check ----------------
    if idempotency_key:
        if not (0 < len(idempotency_key) <= 64):
            raise ValidationError("Invalid idempotency key length.")
        payload_hash = _hash_payload(request_payload_for_hash or items_payload)

        existing = (
            IdempotencyRecord.objects
            .select_for_update()
            .filter(branch=branch, idempotency_key=idempotency_key)
            .first()
        )
        if existing:
            if existing.request_hash != payload_hash:
                raise ValidationError(
                    "This idempotency key was already used with a different payload."
                )
            sale = Sale.objects.filter(pk=existing.response_sale_id).first()
            if sale:
                return sale, True
            raise ValidationError("Idempotency record is inconsistent.")

    # ---------------- Load business settings ----------------
    settings = BusinessSettings.objects.select_related("primary_currency").get(
        business_id=branch.business_id,
    )
    currency = settings.primary_currency
    decimals = currency.decimal_places

    now = timezone.now()

    # ---------------- Validate & build line items ----------------
    if not items_payload:
        raise ValidationError("A sale must contain at least one item.")

    # Fetch all products in one query (avoid N+1)
    product_ids = [int(i["product_id"]) for i in items_payload]
    products = {
        p.id: p for p in Product.objects.filter(
            id__in=product_ids, business_id=branch.business_id,
        ).select_related("category")
    }

    if len(products) != len(set(product_ids)):
        raise ValidationError("One or more products do not exist in this business.")

    lines = []
    subtotal = Decimal("0")
    discount_total = Decimal("0")

    for payload in items_payload:
        product = products[int(payload["product_id"])]
        _validate_product_available(branch, product)

        try:
            qty = Decimal(str(payload["quantity"]))
        except Exception:
            raise ValidationError("Invalid quantity.")
        if qty <= 0:
            raise ValidationError(
                f"Quantity for '{product.name}' must be greater than zero."
            )

        price_row = product.current_price(at=now)
        if price_row is None:
            raise ValidationError(
                f"No active price found for '{product.name}'."
            )
        unit_price = price_row.selling_price

        try:
            line_discount = Decimal(str(payload.get("discount_amount", "0")))
        except Exception:
            raise ValidationError("Invalid discount amount.")
        if line_discount < 0:
            raise ValidationError("Discount cannot be negative.")

        raw_line = (unit_price * qty) - line_discount
        if raw_line < 0:
            raise ValidationError(
                f"Discount exceeds line total for '{product.name}'."
            )

        line_total = _round(raw_line, decimals)
        subtotal += unit_price * qty
        discount_total += line_discount

        lines.append({
            "product": product,
            "product_name": product.name,
            "sku": product.sku,
            "quantity": qty,
            "unit_price": _round(unit_price, decimals),
            "discount_amount": _round(line_discount, decimals),
            "line_total": line_total,
        })

    subtotal = _round(subtotal, decimals)
    discount_total = _round(discount_total, decimals)
    total_amount = _round(subtotal - discount_total, decimals)

    if total_amount < 0:
        raise ValidationError("Total amount cannot be negative.")

    # ---------------- Transaction number ----------------
    sequence = _reserve_next_sequence(branch, now)
    txn_number = _build_transaction_number(branch, sequence, now)

    # ---------------- Create Sale ----------------
    sale = Sale.objects.create(
        business=branch.business,
        branch=branch,
        cashier=user,
        transaction_number=txn_number,
        status=SaleStatus.COMPLETED,
        subtotal=subtotal,
        discount_total=discount_total,
        total_amount=total_amount,
        currency_code=currency.code,
        currency_decimal_places=decimals,
        idempotency_key=idempotency_key,
        note=note,
        completed_at=now,
    )

    # ---------------- Create line items ----------------
    SaleItem.objects.bulk_create([
        SaleItem(sale=sale, **line) for line in lines
    ])

    # ---------------- Audit ----------------
    log_action(
        business=branch.business,
        branch=branch,
        user=user,
        action="sale_created",
        target_type="Sale",
        target_id=str(sale.pk),
        metadata={
            "transaction_number": txn_number,
            "total": str(total_amount),
            "currency": currency.code,
            "items_count": len(lines),
        },
    )

    # ---------------- Inventory consumption ----------------
    from apps.inventory.services import consume_for_sale
    consume_for_sale(sale=sale, performed_by=user)
    

    # ---------------- Idempotency record ----------------
    if idempotency_key:
        IdempotencyRecord.objects.create(
            branch=branch,
            idempotency_key=idempotency_key,
            request_hash=_hash_payload(request_payload_for_hash or items_payload),
            response_sale_id=sale.pk,
            response_status=201,
        )

    return sale, False