from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.audit.services import log_action
from apps.branches.models import Branch
from apps.payments.models import Payment
from apps.settings.models import BusinessSettings

from .models import (
    CashMovement,
    CashMovementType,
    CashSession,
    CashSessionStatus,
)


def _round(value: Decimal, places: int) -> Decimal:
    quant = Decimal(1).scaleb(-places)
    return value.quantize(quant, rounding=ROUND_HALF_UP)


# ------------------------------------------------------------------------------
# Open
# ------------------------------------------------------------------------------
@transaction.atomic
def open_cash_session(
    *, branch: Branch, cashier, opening_balance: Decimal,
) -> CashSession:
    """
    Open a new cash session. Only one OPEN session per (branch, cashier).
    """
    if branch.business_id != cashier.business_id:
        raise ValidationError("Branch does not belong to your business.")
    if cashier.role != "owner" and branch.id not in cashier.assigned_branch_ids:
        raise ValidationError("You are not assigned to this branch.")

    if CashSession.objects.filter(
        branch=branch, cashier=cashier, status=CashSessionStatus.OPEN,
    ).exists():
        raise ValidationError("You already have an open session at this branch.")

    settings = BusinessSettings.objects.select_related("primary_currency").get(
        business_id=branch.business_id,
    )
    currency = settings.primary_currency

    session = CashSession.objects.create(
        branch=branch,
        cashier=cashier,
        opening_balance=opening_balance,
        currency_code=currency.code,
        currency_decimal_places=currency.decimal_places,
    )

    log_action(
        business=branch.business,
        branch=branch,
        user=cashier,
        action="cash_session_opened",
        target_type="CashSession",
        target_id=str(session.pk),
        metadata={"opening_balance": str(opening_balance)},
    )
    return session


# ------------------------------------------------------------------------------
# Compute expected cash (used at close AND in live dashboards)
# ------------------------------------------------------------------------------
def compute_expected_cash(session: CashSession) -> dict:
    """
    Expected Cash = Opening + Cash Sales + Cash Inflows
                    − Cash Refunds − Cash Expenses − Cash Withdrawals
    Non-cash payments are EXCLUDED (they must not affect the drawer).
    """
    decimals = session.currency_decimal_places

    opening = session.opening_balance

    cash_sales = (
        Payment.objects.filter(
            sale__branch=session.branch,
            received_by=session.cashier,
            method="cash",
            is_refund=False,
            created_at__gte=session.opened_at,
        )
        .aggregate(s=Sum("amount"))["s"] or Decimal("0")
    )
    cash_refunds = (
        Payment.objects.filter(
            sale__branch=session.branch,
            received_by=session.cashier,
            method="cash",
            is_refund=True,
            created_at__gte=session.opened_at,
        )
        .aggregate(s=Sum("amount"))["s"] or Decimal("0")
    )

    manual_inflow = (
        session.movements.filter(movement_type=CashMovementType.INFLOW)
        .aggregate(s=Sum("amount"))["s"] or Decimal("0")
    )
    manual_withdrawal = (
        session.movements.filter(movement_type=CashMovementType.WITHDRAWAL)
        .aggregate(s=Sum("amount"))["s"] or Decimal("0")
    )
    manual_expense = (
        session.movements.filter(movement_type=CashMovementType.EXPENSE)
        .aggregate(s=Sum("amount"))["s"] or Decimal("0")
    )

    expected = (
        opening + cash_sales + manual_inflow
        - cash_refunds - manual_expense - manual_withdrawal
    )

    return {
        "opening": _round(opening, decimals),
        "cash_sales": _round(cash_sales, decimals),
        "cash_refunds": _round(cash_refunds, decimals),
        "cash_inflows": _round(manual_inflow, decimals),
        "cash_expenses": _round(manual_expense, decimals),
        "cash_withdrawals": _round(manual_withdrawal, decimals),
        "expected_cash": _round(expected, decimals),
    }


# ------------------------------------------------------------------------------
# Record a manual movement
# ------------------------------------------------------------------------------
@transaction.atomic
def add_cash_movement(
    *, session: CashSession, movement_type: str, amount: Decimal,
    reason: str, reference: str = "", performed_by,
) -> CashMovement:
    if session.status != CashSessionStatus.OPEN:
        raise ValidationError("Cannot modify a closed session (CASH-011).")
    if movement_type not in CashMovementType.values:
        raise ValidationError(f"Invalid movement type: '{movement_type}'.")
    if amount <= 0:
        raise ValidationError("Amount must be positive.")
    if not reason.strip():
        raise ValidationError("A reason is required for every cash movement.")

    movement = CashMovement.objects.create(
        session=session,
        movement_type=movement_type,
        amount=amount,
        reason=reason,
        reference=reference,
        performed_by=performed_by,
    )

    log_action(
        business=session.branch.business,
        branch=session.branch,
        user=performed_by,
        action="cash_movement",
        target_type="CashSession",
        target_id=str(session.pk),
        metadata={
            "movement_type": movement_type,
            "amount": str(amount),
            "reason": reason,
        },
    )
    return movement


# ------------------------------------------------------------------------------
# Close session
# ------------------------------------------------------------------------------
@transaction.atomic
def close_cash_session(
    *, session: CashSession, actual_cash: Decimal, explanation: str = "",
    closed_by,
) -> CashSession:
    if session.status == CashSessionStatus.CLOSED:
        raise ValidationError("This session is already closed.")

    decimals = session.currency_decimal_places
    computed = compute_expected_cash(session)
    expected = computed["expected_cash"]

    actual = _round(actual_cash, decimals)
    difference = _round(actual - expected, decimals)

    if difference != 0 and not explanation.strip():
        raise ValidationError(
            "A discrepancy explanation is required when actual cash "
            "differs from expected cash."
        )

    session.status = CashSessionStatus.CLOSED
    session.expected_cash = expected
    session.actual_cash = actual
    session.difference = difference
    session.discrepancy_explanation = explanation
    session.closed_at = timezone.now()
    session.closed_by = closed_by
    session.save(update_fields=[
        "status", "expected_cash", "actual_cash", "difference",
        "discrepancy_explanation", "closed_at", "closed_by",
    ])
    if difference != 0:
        from apps.notifications.services import notify_cash_discrepancy
        try:
            notify_cash_discrepancy(session=session, difference=difference)
        except Exception:
            pass

    log_action(
        business=session.branch.business,
        branch=session.branch,
        user=closed_by,
        action="cash_session_closed",
        target_type="CashSession",
        target_id=str(session.pk),
        metadata={
            "expected": str(expected),
            "actual": str(actual),
            "difference": str(difference),
            "explanation": explanation,
        },
    )
    return session