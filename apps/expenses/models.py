from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone


class ExpenseCategory(models.Model):
    business = models.ForeignKey(
        "businesses.Business",
        on_delete=models.CASCADE,
        related_name="expense_categories",
    )
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("business", "name")
        ordering = ["name"]
        verbose_name_plural = "Expense categories"

    def __str__(self) -> str:
        return self.name


class ExpenseStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"


class Expense(models.Model):
    """
    Operational expense (EXP-001..EXP-011).
    - Currency is snapshotted.
    - Approved expenses are immutable (EXP-011).
    - Must be associated with the business and, where applicable, a branch
      (EXP-003, BRULE-012).
    """
    business = models.ForeignKey(
        "businesses.Business",
        on_delete=models.PROTECT,
        related_name="expenses",
    )
    branch = models.ForeignKey(
        "branches.Branch",
        on_delete=models.PROTECT,
        related_name="expenses",
        null=True,
        blank=True,
        help_text="Null = central/business-level expense.",
    )
    category = models.ForeignKey(
        ExpenseCategory,
        on_delete=models.PROTECT,
        related_name="expenses",
    )
    amount = models.DecimalField(
        max_digits=14, decimal_places=4,
        validators=[MinValueValidator(Decimal("0.0001"))],
    )
    currency_code = models.CharField(max_length=3)
    currency_decimal_places = models.PositiveSmallIntegerField(default=2)

    description = models.TextField()
    expense_date = models.DateField(default=timezone.localdate)
    attachment = models.FileField(
        upload_to="expenses/attachments/", blank=True, null=True,
    )

    status = models.CharField(
        max_length=20, choices=ExpenseStatus.choices,
        default=ExpenseStatus.PENDING,
    )
    submitted_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        related_name="expenses_submitted",
    )
    reviewed_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="expenses_reviewed",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_note = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["business", "status"]),
            models.Index(fields=["branch", "status"]),
            models.Index(fields=["expense_date"]),
        ]

    def __str__(self) -> str:
        return f"{self.amount} {self.currency_code} - {self.category.name}"