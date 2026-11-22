from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.accounts.access import can_access_branch
from apps.audit.services import log_action
from apps.branches.models import Branch
from apps.settings.models import BusinessSettings

from .models import Expense, ExpenseCategory, ExpenseStatus


def _round(value: Decimal, places: int) -> Decimal:
    quant = Decimal(1).scaleb(-places)
    return value.quantize(quant, rounding=ROUND_HALF_UP)


def _shop_today(user):
    from datetime import datetime

    from apps.settings.regional import business_zone

    return datetime.now(business_zone(user.business_id)).date()


def _check_date(user, expense_date):
    """Default to today AT THE SHOP; an expense can't be dated in the future."""
    today = _shop_today(user)
    if expense_date is None:
        return today
    if expense_date > today:
        raise ValidationError({"expense_date": "An expense can't be dated in the future."})
    return expense_date


def _check_branch(user, branch):
    from apps.accounts.access import has_business_wide_access

    if branch is None:
        # Business-wide (central) expenses are for people who see every branch.
        if not has_business_wide_access(user):
            raise ValidationError({"branch": "Choose one of your branches."})
        return
    if branch.business_id != user.business_id:
        raise ValidationError({"branch": "Branch not found."})
    if not can_access_branch(user, branch):
        raise ValidationError({"branch": "You are not assigned to this branch."})


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

    _check_branch(user, branch)
    expense_date = _check_date(user, expense_date)
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
        expense_date=expense_date,
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

EDITABLE_FIELDS = ("category", "amount", "description", "branch", "expense_date", "attachment")


@transaction.atomic
def update_expense(*, expense: Expense, user, changes: dict) -> Expense:
    """
    Correct a PENDING expense (the submitter or the owner). Approved and
    rejected expenses are final (EXP-011); every change is audited.
    """
    if expense.status != ExpenseStatus.PENDING:
        raise ValidationError(
            f"This expense is {expense.get_status_display().lower()} and can no longer be changed."
        )
    if expense.submitted_by_id != user.id and user.role != "owner":
        raise PermissionDenied("Only the person who filed it or the owner can change it.")

    before = {}
    if "category" in changes:
        category = ExpenseCategory.objects.filter(
            pk=changes["category"], business_id=user.business_id).first()
        if category is None:
            raise ValidationError({"category": "Category not found."})
        if not category.is_active and category.pk != expense.category_id:
            raise ValidationError({"category": "This category is deactivated."})
        before["category"] = expense.category.name
        expense.category = category
    if "branch" in changes:
        branch = None
        if changes["branch"] is not None:
            branch = Branch.objects.filter(pk=changes["branch"]).first()
            if branch is None:
                raise ValidationError({"branch": "Branch not found."})
        _check_branch(user, branch)
        before["branch"] = expense.branch.name if expense.branch else None
        expense.branch = branch
    if "amount" in changes:
        if changes["amount"] <= 0:
            raise ValidationError({"amount": "Amount must be positive."})
        before["amount"] = str(expense.amount)
        expense.amount = _round(changes["amount"], expense.currency_decimal_places)
    if "expense_date" in changes:
        before["expense_date"] = str(expense.expense_date)
        expense.expense_date = _check_date(user, changes["expense_date"])
    if "description" in changes:
        before["description"] = expense.description
        expense.description = changes["description"]
    if "attachment" in changes:
        before["attachment"] = expense.attachment.name or None
        expense.attachment = changes["attachment"]
    if not before:
        return expense
    expense.save()

    log_action(
        business=expense.business,
        branch=expense.branch,
        user=user,
        action="expense_updated",
        target_type="Expense",
        target_id=str(expense.pk),
        metadata={"changed": sorted(before), "before": before},
    )
    return expense
