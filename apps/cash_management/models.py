from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone


class CashSessionStatus(models.TextChoices):
    OPEN = "open", "Open"
    CLOSED = "closed", "Closed"


class CashSession(models.Model):
    """
    A cashier's cash-management period at a branch (CASH-001..CASH-012).
    Only ONE session per (branch, cashier) may be open at a time.
    """
    branch = models.ForeignKey(
        "branches.Branch",
        on_delete=models.PROTECT,
        related_name="cash_sessions",
    )
    cashier = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        related_name="cash_sessions",
    )
    status = models.CharField(
        max_length=20, choices=CashSessionStatus.choices,
        default=CashSessionStatus.OPEN,
    )

    # Opening
    opening_balance = models.DecimalField(
        max_digits=14, decimal_places=4, default=Decimal("0"),
        validators=[MinValueValidator(Decimal("0"))],
    )
    opened_at = models.DateTimeField(default=timezone.now)

    # Closing
    expected_cash = models.DecimalField(
        max_digits=14, decimal_places=4, null=True, blank=True,
    )
    actual_cash = models.DecimalField(
        max_digits=14, decimal_places=4, null=True, blank=True,
    )
    difference = models.DecimalField(
        max_digits=14, decimal_places=4, null=True, blank=True,
    )
    discrepancy_explanation = models.TextField(blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    closed_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="closed_cash_sessions",
    )

    currency_code = models.CharField(max_length=3)
    currency_decimal_places = models.PositiveSmallIntegerField(default=2)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-opened_at"]
        indexes = [
            models.Index(fields=["branch", "status"]),
            models.Index(fields=["cashier", "status"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["branch", "cashier"],
                condition=models.Q(status="open"),
                name="uniq_open_session_per_branch_cashier",
            ),
        ]

    def __str__(self) -> str:
        return f"Session #{self.pk} - {self.cashier.username} @ {self.branch.name}"


class CashMovementType(models.TextChoices):
    INFLOW = "inflow", "Cash Inflow"
    WITHDRAWAL = "withdrawal", "Cash Withdrawal"
    REFUND = "refund", "Cash Refund"
    EXPENSE = "expense", "Cash Expense"


class CashMovement(models.Model):
    """
    A manual cash movement inside a session (CASH-005).
    Cash sales and cash refunds are recorded automatically by the payment
    service — they show up here so reconciliation is complete.
    """
    session = models.ForeignKey(
        CashSession,
        on_delete=models.CASCADE,
        related_name="movements",
    )
    movement_type = models.CharField(max_length=20, choices=CashMovementType.choices)
    amount = models.DecimalField(
        max_digits=14, decimal_places=4,
        validators=[MinValueValidator(Decimal("0.0001"))],
    )
    reason = models.TextField()
    reference = models.CharField(max_length=100, blank=True)

    # Optional linkage back to the originating payment / sale
    payment = models.ForeignKey(
        "payments.Payment",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="cash_movements",
    )

    performed_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        related_name="cash_movements",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["session", "movement_type"]),
        ]

    def __str__(self) -> str:
        return f"{self.get_movement_type_display()}: {self.amount}"