from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.audit.services import log_action
from apps.sales.models import Sale, SaleStatus
from apps.settings.models import BusinessSettings

from .models import Payment, PaymentMethod


def _round(value: Decimal, places: int) -> Decimal:
    quant = Decimal(1).scaleb(-places)
    return value.quantize(quant, rounding=ROUND_HALF_UP)


def _open_session_for(*, branch_id, user):
    """The user's open cash session at this branch, or None."""
    from apps.cash_management.models import CashSession, CashSessionStatus

    return CashSession.objects.filter(
        branch_id=branch_id, cashier=user, status=CashSessionStatus.OPEN,
    ).first()


def _refund_cash_session(*, original: Payment, sale: Sale, user):
    """
    Pick the drawer a cash refund is paid out of:
      1. the drawer the original cash payment went into, if still open
         (the money is normally handed back from the same till);
      2. otherwise the refunding user's own open drawer at this branch;
      3. otherwise None — the refund is recorded but belongs to no drawer.
    """
    from apps.cash_management.models import CashSessionStatus

    if original.cash_session_id and original.cash_session.status == CashSessionStatus.OPEN:
        return original.cash_session
    return _open_session_for(branch_id=sale.branch_id, user=user)


# ------------------------------------------------------------------------------
# Add payment(s) to a sale
# ------------------------------------------------------------------------------
@transaction.atomic
def add_payments_to_sale(
    *, sale: Sale, payments_payload: list[dict], user,
) -> list[Payment]:
    """
    Add one or more payments to a sale atomically.

    Rules:
      - Sale must be COMPLETED.
      - Sum of new payments + existing must not exceed total_amount.
      - Cash overpayment is allowed up to `total_amount`; any excess is
        considered change and NOT stored as payment.
      - Each payment method must be one of the configured enum values.
      - For non-cash methods, `reference` is required.

    NOTE: Cash reconciliation reads `Payment` rows directly via
    apps/cash_management/services.py::compute_expected_cash. We do NOT
    create `CashMovement` rows here for cash sales — doing so would
    double-count them in the drawer balance.
    """
    if sale.status != SaleStatus.COMPLETED:
        raise ValidationError(
            f"Cannot add payments to a {sale.get_status_display().lower()} sale."
        )

    if not payments_payload:
        raise ValidationError("At least one payment is required.")

    settings = BusinessSettings.objects.select_related("primary_currency").get(
        business_id=sale.business_id,
    )
    decimals = settings.primary_currency.decimal_places
    currency_code = settings.primary_currency.code

    # Guard against cross-currency mismatch
    if sale.currency_code != currency_code:
        raise ValidationError(
            "This sale was created in a different currency. "
            "Payments must match the sale's currency."
        )

    existing_paid = (
        Payment.objects.filter(sale=sale, is_refund=False)
        .aggregate(s=Sum("amount"))["s"] or Decimal("0")
    )
    already_paid = _round(existing_paid, decimals)
    remaining = _round(sale.total_amount - already_paid, decimals)

    if remaining <= 0:
        raise ValidationError("This sale is already fully paid.")

    total_new = Decimal("0")
    created: list[Payment] = []
    cash_session = _open_session_for(branch_id=sale.branch_id, user=user)

    for raw in payments_payload:
        method = raw.get("method")
        if method not in PaymentMethod.values:
            raise ValidationError(f"Unsupported payment method: '{method}'.")

        try:
            amount = Decimal(str(raw.get("amount", "0")))
        except Exception:
            raise ValidationError("Invalid payment amount.")
        if amount <= 0:
            raise ValidationError("Payment amount must be greater than zero.")

        reference = (raw.get("reference") or "").strip()
        if method != PaymentMethod.CASH and not reference:
            raise ValidationError(
                f"A reference is required for '{method}' payments."
            )

        amount = _round(amount, decimals)
        total_new += amount

        created.append(Payment(
            sale=sale,
            method=method,
            amount=amount,
            currency_code=currency_code,
            currency_decimal_places=decimals,
            reference=reference,
            received_by=user,
            cash_session=cash_session if method == PaymentMethod.CASH else None,
        ))

    total_new = _round(total_new, decimals)
    if total_new > remaining:
        raise ValidationError(
            f"Total payments exceed the remaining amount "
            f"({total_new} > {remaining})."
        )

    Payment.objects.bulk_create(created)

    log_action(
        business=sale.business,
        branch=sale.branch,
        user=user,
        action="sale_payment_added",
        target_type="Sale",
        target_id=str(sale.pk),
        metadata={
            "transaction_number": sale.transaction_number,
            "amount": str(total_new),
            "methods": [p.method for p in created],
        },
    )

    return created


# ------------------------------------------------------------------------------
# Refund
# ------------------------------------------------------------------------------
@transaction.atomic
def refund_sale(*, sale: Sale, user, amount: Decimal | None = None,
                reason: str = "", restock: bool = False) -> Payment:
    """
    Create a refund against a sale.

    Rules (BRULE-013):
      - Refund must reference the original sale.
      - Refund amount cannot exceed the net paid amount.
      - Sale is marked REFUNDED if fully refunded.
      - `restock=True` puts the sold goods back into inventory. Only allowed
        on the refund that fully refunds the sale: refunds are amount-based,
        so a partial refund can't say which items came back. Refunded drinks
        are usually not resellable, hence the default of False.

    NOTE: Like `add_payments_to_sale`, we do NOT create `CashMovement`
    rows here. `compute_expected_cash` reads refund `Payment` rows via
    their `cash_session` link so cash refunds reduce the right drawer.
    """
    if sale.status not in (SaleStatus.COMPLETED, SaleStatus.REFUNDED):
        raise ValidationError("This sale cannot be refunded.")

    settings = BusinessSettings.objects.select_related("primary_currency").get(
        business_id=sale.business_id,
    )
    decimals = settings.primary_currency.decimal_places

    paid = (
        Payment.objects.filter(sale=sale, is_refund=False)
        .aggregate(s=Sum("amount"))["s"] or Decimal("0")
    )
    refunded = (
        Payment.objects.filter(sale=sale, is_refund=True)
        .aggregate(s=Sum("amount"))["s"] or Decimal("0")
    )
    remaining = _round(paid - refunded, decimals)

    if remaining <= 0:
        raise ValidationError("This sale has no refundable balance.")

    if amount is None:
        amount = remaining
    else:
        try:
            amount = Decimal(str(amount))
        except Exception:
            raise ValidationError("Invalid refund amount.")
        amount = _round(amount, decimals)

    if amount <= 0:
        raise ValidationError("Refund amount must be positive.")
    if amount > remaining:
        raise ValidationError(
            f"Refund amount exceeds remaining balance ({amount} > {remaining})."
        )

    # Match the refund to the most recent completed non-refund payment
    original = (
        Payment.objects.filter(sale=sale, is_refund=False)
        .order_by("-created_at").first()
    )
    if original is None:
        raise ValidationError("No original payment to refund against.")

    is_full_refund = refunded + amount >= paid
    if restock and not is_full_refund:
        raise ValidationError(
            "Restocking is only possible when the sale is fully refunded."
        )

    refund_session = None
    if original.method == PaymentMethod.CASH:
        refund_session = _refund_cash_session(original=original, sale=sale, user=user)

    refund = Payment.objects.create(
        sale=sale,
        method=original.method,
        amount=amount,
        currency_code=sale.currency_code,
        currency_decimal_places=sale.currency_decimal_places,
        is_refund=True,
        refunded_payment=original,
        received_by=user,
        reference=f"REF-{sale.transaction_number}",
        cash_session=refund_session,
    )

    # Status update
    if is_full_refund:
        sale.status = SaleStatus.REFUNDED
        sale.save(update_fields=["status"])

    if restock:
        from apps.inventory.services import restore_stock_for_sale
        restore_stock_for_sale(
            sale=sale, performed_by=user,
            reason=f"Refund of {sale.transaction_number}: {reason}".strip(": "),
        )

    log_action(
        business=sale.business,
        branch=sale.branch,
        user=user,
        action="sale_refunded",
        target_type="Sale",
        target_id=str(sale.pk),
        metadata={
            "transaction_number": sale.transaction_number,
            "refund_amount": str(amount),
            "reason": reason,
            "refund_payment_id": str(refund.pk),
            "restocked": restock,
            "cash_session_id": refund_session.pk if refund_session else None,
        },
    )

    return refund


# ------------------------------------------------------------------------------
# Cancellation (pre-payment only)
# ------------------------------------------------------------------------------
@transaction.atomic
def cancel_sale(*, sale: Sale, user, reason: str = "") -> Sale:
    """
    Cancel a sale that has no payments yet.
    Paid sales must be refunded instead.
    """
    if sale.status == SaleStatus.CANCELLED:
        raise ValidationError("This sale is already cancelled.")
    if sale.status == SaleStatus.REFUNDED:
        raise ValidationError("This sale was already refunded.")

    paid = (
        Payment.objects.filter(sale=sale, is_refund=False).exists()
    )
    if paid:
        raise ValidationError(
            "This sale has payments. Refund it instead of cancelling."
        )

    sale.status = SaleStatus.CANCELLED
    sale.cancelled_at = timezone.now()
    sale.cancelled_by = user
    sale.cancellation_reason = reason
    sale.save(update_fields=[
        "status", "cancelled_at", "cancelled_by", "cancellation_reason",
    ])

    # The sale never happened, so the stock it consumed goes back (BRULE-008).
    from apps.inventory.services import restore_stock_for_sale
    restore_stock_for_sale(
        sale=sale, performed_by=user,
        reason=f"Cancellation of {sale.transaction_number}: {reason}".strip(": "),
    )

    log_action(
        business=sale.business,
        branch=sale.branch,
        user=user,
        action="sale_cancelled",
        target_type="Sale",
        target_id=str(sale.pk),
        metadata={
            "transaction_number": sale.transaction_number,
            "reason": reason,
        },
    )

    return sale