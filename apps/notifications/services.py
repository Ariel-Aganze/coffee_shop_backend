from django.utils import timezone

from .models import Notification, NotificationType

from django.db import models
from django.utils import timezone


# ------------------------------------------------------------------------------
# Core: create a notification for a user
# ------------------------------------------------------------------------------
def _num(value) -> str:
    """4.0000 → "4", 14500.5000 → "14,500.5": readable in a sentence."""
    from decimal import Decimal

    d = Decimal(str(value)).normalize()
    whole, _, frac = f"{d:f}".partition(".")
    return f"{int(whole):,}" + (f".{frac}" if frac else "")


def _name(user) -> str:
    return user.get_full_name() or user.username


def notify(
    *,
    recipient,
    notification_type: str,
    title: str,
    message: str,
    branch=None,
    metadata: dict | None = None,
) -> Notification:
    """
    Single entry point for creating a notification.
    Business is taken from the recipient to guarantee isolation.
    """
    return Notification.objects.create(
        recipient=recipient,
        business_id=recipient.business_id,
        branch=branch,
        notification_type=notification_type,
        title=title,
        message=message,
        metadata=metadata or {},
    )


def notify_owners_and_managers(
    *,
    business_id: int,
    notification_type: str,
    title: str,
    message: str,
    branch=None,
    metadata: dict | None = None,
):
    """
    Fan out to every owner and branch manager of a business.
    If a branch is given, only branch managers assigned to that branch are
    notified; owners are always notified.
    """
    from apps.accounts.constants import UserRole
    from apps.accounts.models import User

    recipients = User.objects.filter(
        business_id=business_id,
        is_active=True,
        role__in=[UserRole.OWNER, UserRole.BRANCH_MANAGER],
    )
    if branch is not None:
        recipients = recipients.filter(
            # Owners OR branch managers assigned to this branch
            models.Q(role=UserRole.OWNER)
            | models.Q(
                role=UserRole.BRANCH_MANAGER,
                branch_assignments__branch_id=branch.id,
            )
        ).distinct()

    notifications = []
    for user in recipients:
        notifications.append(notify(
            recipient=user,
            notification_type=notification_type,
            title=title,
            message=message,
            branch=branch,
            metadata=metadata,
        ))
    return notifications


# ------------------------------------------------------------------------------
# Event-specific helpers
# ------------------------------------------------------------------------------
def notify_low_stock(*, branch, product, current_quantity, threshold):
    """NOT-001."""
    from apps.accounts.constants import UserRole
    from apps.accounts.models import User

    recipients = User.objects.filter(
        business_id=branch.business_id,
        is_active=True,
        role__in=[UserRole.OWNER, UserRole.BRANCH_MANAGER, UserRole.INVENTORY_MANAGER],
    )
    for user in recipients:
        notify(
            recipient=user,
            notification_type=NotificationType.LOW_STOCK,
            title=f"Low stock: {product.name}",
            message=(
                f"{product.name} at {branch.name}: {_num(current_quantity)} left "
                f"(minimum {_num(threshold)})."
            ),
            branch=branch,
            metadata={
                "product_id": product.id,
                "sku": product.sku,
                "quantity": str(current_quantity),
                "threshold": str(threshold),
            },
        )


def notify_cash_discrepancy(*, session, difference):
    """NOT-005."""
    from apps.accounts.constants import UserRole
    from apps.accounts.models import User

    recipients = User.objects.filter(
        business_id=session.branch.business_id,
        is_active=True,
        role__in=[UserRole.OWNER, UserRole.BRANCH_MANAGER, UserRole.ACCOUNTANT],
    )
    for user in recipients:
        notify(
            recipient=user,
            notification_type=NotificationType.CASH_DISCREPANCY,
            title="Cash discrepancy detected",
            message=(
                f"{_name(session.cashier)}'s drawer at {session.branch.name} closed "
                f"{'over' if difference > 0 else 'short'} by "
                f"{_num(abs(difference))} {session.currency_code}."
            ),
            branch=session.branch,
            metadata={
                "session_id": session.pk,
                "difference": str(difference),
                "expected": str(session.expected_cash),
                "actual": str(session.actual_cash),
            },
        )


def notify_pending_approval(*, expense):
    """NOT-003."""
    notify_owners_and_managers(
        business_id=expense.business_id,
        notification_type=NotificationType.PENDING_APPROVAL,
        title=f"Pending approval: {expense.category.name}",
        message=(
            f"{_name(expense.submitted_by)} recorded {_num(expense.amount)} "
            f"{expense.currency_code} ({expense.description[:60]}) — it needs your review."
        ),
        branch=expense.branch,
        metadata={
            "expense_id": expense.pk,
            "amount": str(expense.amount),
            "currency": expense.currency_code,
        },
    )