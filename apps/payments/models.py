from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone


class PaymentMethod(models.TextChoices):
    CASH = "cash", "Cash"
    MOBILE_MONEY = "mobile_money", "Mobile Money"
    BANK_CARD = "bank_card", "Bank Card"
    BANK_TRANSFER = "bank_transfer", "Bank Transfer"
    OTHER = "other", "Other"


class Payment(models.Model):
    """
    A payment against a sale.
    A sale may have multiple payments (split payment, POS-010).
    Refunds are represented as negative-amount payments with
    `is_refund=True` (BRULE-013: must reference the original sale).
    """
    sale = models.ForeignKey(
        "sales.Sale",
        on_delete=models.PROTECT,
        related_name="payments",
    )
    method = models.CharField(max_length=30, choices=PaymentMethod.choices)
    amount = models.DecimalField(
        max_digits=14, decimal_places=4,
        validators=[MinValueValidator(Decimal("0"))],
    )
    currency_code = models.CharField(max_length=3)
    currency_decimal_places = models.PositiveSmallIntegerField(default=2)

    # For mobile_money / bank_card / bank_transfer
    reference = models.CharField(
        max_length=100, blank=True,
        help_text="External reference (e.g. M-Pesa code, card auth code).",
    )

    is_refund = models.BooleanField(default=False)
    refunded_payment = models.ForeignKey(
        "self", on_delete=models.PROTECT, null=True, blank=True,
        related_name="refunds",
        help_text="Required when is_refund=True.",
    )

    received_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        related_name="payments_received",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["sale"]),
            models.Index(fields=["method"]),
        ]

    def __str__(self) -> str:
        sign = "-" if self.is_refund else ""
        return f"{sign}{self.amount} {self.currency_code} via {self.get_method_display()}"