from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone


class SaleStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    COMPLETED = "completed", "Completed"
    CANCELLED = "cancelled", "Cancelled"
    REFUNDED = "refunded", "Refunded"


class Sale(models.Model):
    """
    A sale transaction at a branch.

    Rules:
      - `branch` must belong to `business` (enforced at creation).
      - `cashier` must be an active user.
      - `currency_code` is snapshotted at sale time so historical reports
        remain accurate even if the business switches primary currency later
        (CUR-017, CUR-018, BRULE-019, BRULE-020).
      - `total_amount` is authoritative and calculated on the backend.
    """
    business = models.ForeignKey(
        "businesses.Business",
        on_delete=models.PROTECT,
        related_name="sales",
    )
    branch = models.ForeignKey(
        "branches.Branch",
        on_delete=models.PROTECT,
        related_name="sales",
    )
    cashier = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        related_name="sales_made",
    )
    transaction_number = models.CharField(max_length=32, unique=True)

    status = models.CharField(
        max_length=20, choices=SaleStatus.choices, default=SaleStatus.COMPLETED,
    )

    # Money
    subtotal = models.DecimalField(max_digits=14, decimal_places=4, default=Decimal("0"))
    discount_total = models.DecimalField(max_digits=14, decimal_places=4, default=Decimal("0"))
    total_amount = models.DecimalField(max_digits=14, decimal_places=4, default=Decimal("0"))

    # Currency snapshot
    currency_code = models.CharField(max_length=3)
    currency_decimal_places = models.PositiveSmallIntegerField(default=2)

    # Idempotency
    idempotency_key = models.CharField(
        max_length=64, null=True, blank=True,
        help_text="Client-supplied key to prevent duplicate submissions.",
    )

    note = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="+",
    )
    cancellation_reason = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["business", "created_at"]),
            models.Index(fields=["branch", "created_at"]),
            models.Index(fields=["cashier", "created_at"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["branch", "idempotency_key"],
                name="uniq_branch_idempotency_key",
                condition=models.Q(idempotency_key__isnull=False),
            ),
        ]

    def __str__(self) -> str:
        return f"{self.transaction_number} - {self.total_amount} {self.currency_code}"

        # ---------- Money helpers ----------
    def paid_amount(self):
        """
        Sum of positive (non-refund) payments.
        """
        from django.db.models import Sum
        from apps.payments.models import Payment

        return (
            Payment.objects.filter(sale=self, is_refund=False)
            .aggregate(s=Sum("amount"))["s"] or Decimal("0")
        )

    def refunded_amount(self):
        from django.db.models import Sum
        from apps.payments.models import Payment

        return (
            Payment.objects.filter(sale=self, is_refund=True)
            .aggregate(s=Sum("amount"))["s"] or Decimal("0")
        )

    def net_paid(self) -> Decimal:
        return self.paid_amount() - self.refunded_amount()

    def is_fully_paid(self) -> bool:
        return self.paid_amount() >= self.total_amount

    def is_fully_refunded(self) -> bool:
        return self.refunded_amount() >= self.paid_amount()


class SaleItem(models.Model):
    """
    An immutable line in a sale.

    - `product_name` and `sku` are snapshotted.
    - `unit_price` is snapshotted (BRULE-006, POS-024).
    - `line_total` is calculated on the backend (BRULE-005).
    """
    sale = models.ForeignKey(
        Sale,
        on_delete=models.CASCADE,
        related_name="items",
    )
    product = models.ForeignKey(
        "products.Product",
        on_delete=models.PROTECT,
        related_name="sale_items",
    )

    # Snapshots
    product_name = models.CharField(max_length=255)
    sku = models.CharField(max_length=64)

    quantity = models.DecimalField(
        max_digits=12, decimal_places=4,
        validators=[MinValueValidator(Decimal("0.0001"))],
    )
    unit_price = models.DecimalField(
        max_digits=14, decimal_places=4,
        validators=[MinValueValidator(Decimal("0"))],
    )
    discount_amount = models.DecimalField(
        max_digits=14, decimal_places=4, default=Decimal("0"),
        validators=[MinValueValidator(Decimal("0"))],
    )
    line_total = models.DecimalField(
        max_digits=14, decimal_places=4, default=Decimal("0"),
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]

    def __str__(self) -> str:
        return f"{self.quantity} x {self.product_name}"


class IdempotencyRecord(models.Model):
    """
    Tracks every idempotency key submitted for a sale so duplicates can be
    detected before any side-effect is committed (POS-022, BRULE-025).
    """
    branch = models.ForeignKey(
        "branches.Branch", on_delete=models.CASCADE, related_name="+",
    )
    idempotency_key = models.CharField(max_length=64)
    request_hash = models.CharField(max_length=64)
    response_sale_id = models.BigIntegerField(null=True, blank=True)
    response_status = models.PositiveSmallIntegerField(default=0)
    response_body = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("branch", "idempotency_key")
        indexes = [models.Index(fields=["branch", "idempotency_key"])]

class SaleCounter(models.Model):
    """
    Per-branch, per-day monotonic counter used to build transaction numbers
    inside the same transaction as sale creation.
    """
    branch = models.ForeignKey(
        "branches.Branch", on_delete=models.CASCADE, related_name="sale_counters",
    )
    date = models.DateField()
    last_value = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = ("branch", "date")