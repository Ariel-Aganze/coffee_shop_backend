from django.utils import timezone

from .models import Notification, NotificationType

from django.db import models
from django.utils import timezone


# ------------------------------------------------------------------------------
# Core: create a notification for a user
# ------------------------------------------------------------------------------
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
                f"'{product.name}' at {branch.name} is at {current_quantity} "
                f"(threshold: {threshold})."
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
                f"Session #{session.pk} at {session.branch.name} closed with "
                f"a difference of {difference} {session.currency_code}."
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
            f"Expense of {expense.amount} {expense.currency_code} "
            f"submitted by {expense.submitted_by.username} needs review."
        ),
        branch=expense.branch,
        metadata={
            "expense_id": expense.pk,
            "amount": str(expense.amount),
            "currency": expense.currency_code,
        },
    )