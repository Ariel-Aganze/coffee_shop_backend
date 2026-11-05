from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.audit.services import log_action
from apps.branches.models import Branch
from apps.settings.models import BusinessSettings

from .models import Expense, ExpenseCategory, ExpenseStatus


def _round(value: Decimal, places: int) -> Decimal:
    quant = Decimal(1).scaleb(-places)
    return value.quantize(quant, rounding=ROUND_HALF_UP)


@transaction.atomic
def create_expense(
    *,
    user,
    category: ExpenseCategory,
    amount: Decimal,
    description: str,
    branch: Branch | None = None,
    expense_date=None,
    attachment=None,
) -> Expense:
    if category.business_id != user.business_id:
        raise ValidationError("Category does not belong to your business.")
    if not category.is_active:
        raise ValidationError("Category is not active.")

    if branch is not None:
        if branch.business_id != user.business_id:
            raise ValidationError("Branch does not belong to your business.")
        if user.role != "owner" and branch.id not in user.assigned_branch_ids:
            raise ValidationError("You are not assigned to this branch.")

    if amount <= 0:
        raise ValidationError("Amount must be positive.")

    settings = BusinessSettings.objects.select_related("primary_currency").get(
        business_id=user.business_id,
    )
    currency = settings.primary_currency
    amount = _round(amount, currency.decimal_places)

    expense = Expense.objects.create(
        business_id=user.business_id,
        branch=branch,
        category=category,
        amount=amount,
        currency_code=currency.code,
        currency_decimal_places=currency.decimal_places,
        description=description,
        expense_date=expense_date or timezone.localdate(),
        attachment=attachment,
        status=ExpenseStatus.PENDING,
        submitted_by=user,
    )

    log_action(
        business=expense.business,
        branch=branch,
        user=user,
        action="expense_created",
        target_type="Expense",
        target_id=str(expense.pk),
        metadata={
            "amount": str(amount),
            "currency": currency.code,
            "category": category.name,
        },
    )
    from apps.notifications.services import notify_pending_approval
    try:
        notify_pending_approval(expense=expense)
    except Exception:
        pass
    return expense


@transaction.atomic
def approve_expense(*, expense: Expense, user, note: str = "") -> Expense:
    if expense.status != ExpenseStatus.PENDING:
        raise ValidationError(
            f"This expense is already {expense.get_status_display().lower()}."
        )
    if expense.submitted_by_id == user.id and user.role != "owner":
        raise ValidationError("You cannot approve your own expense.")

    expense.status = ExpenseStatus.APPROVED
    expense.reviewed_by = user
    expense.reviewed_at = timezone.now()
    expense.review_note = note
    expense.save(update_fields=[
        "status", "reviewed_by", "reviewed_at", "review_note", "updated_at",
    ])

    log_action(
        business=expense.business,
        branch=expense.branch,
        user=user,
        action="expense_approved",
        target_type="Expense",
        target_id=str(expense.pk),
        metadata={"note": note, "amount": str(expense.amount)},
    )
    return expense


@transaction.atomic
def reject_expense(*, expense: Expense, user, note: str = "") -> Expense:
    if expense.status != ExpenseStatus.PENDING:
        raise ValidationError(
            f"This expense is already {expense.get_status_display().lower()}."
        )
    if not note.strip():
        raise ValidationError("A rejection reason is required.")

    expense.status = ExpenseStatus.REJECTED
    expense.reviewed_by = user
    expense.reviewed_at = timezone.now()
    expense.review_note = note
    expense.save(update_fields=[
        "status", "reviewed_by", "reviewed_at", "review_note", "updated_at",
    ])

    log_action(
        business=expense.business,
        branch=expense.branch,
        user=user,
        action="expense_rejected",
        target_type="Expense",
        target_id=str(expense.pk),
        metadata={"note": note, "amount": str(expense.amount)},
    )
    return expense