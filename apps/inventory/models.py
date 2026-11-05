from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models


class Inventory(models.Model):
    """
    Current stock balance of a product at a branch.
    Do NOT update `.quantity` directly — always go through StockMovement.
    """
    branch = models.ForeignKey(
        "branches.Branch",
        on_delete=models.CASCADE,
        related_name="inventory_items",
    )
    product = models.ForeignKey(
        "products.Product",
        on_delete=models.PROTECT,
        related_name="inventory_records",
    )
    quantity = models.DecimalField(
        max_digits=14, decimal_places=4, default=Decimal("0"),
        validators=[MinValueValidator(Decimal("0"))],
    )
    minimum_threshold = models.DecimalField(
        max_digits=14, decimal_places=4, default=Decimal("0"),
        help_text="Alert when quantity drops at or below this threshold.",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("branch", "product")
        indexes = [models.Index(fields=["branch", "product"])]

    def __str__(self) -> str:
        return f"{self.product.sku} @ {self.branch.name}: {self.quantity}"

    @property
    def is_low_stock(self) -> bool:
        return self.quantity <= self.minimum_threshold


class StockMovementType(models.TextChoices):
    PURCHASE = "purchase", "Purchase Received"
    SALE = "sale", "Sale Consumption"
    ADJUSTMENT = "adjustment", "Manual Adjustment"
    DAMAGED = "damaged", "Damaged Goods"
    EXPIRED = "expired", "Expired Goods"
    RETURN = "return", "Returned Stock"
    TRANSFER_OUT = "transfer_out", "Stock Transfer Out"
    TRANSFER_IN = "transfer_in", "Stock Transfer In"


class StockMovement(models.Model):
    """
    Immutable record of any change to inventory (INV-008, INV-019, BRULE-008).
    Signed `delta`: positive for inflow, negative for outflow.
    """
    branch = models.ForeignKey(
        "branches.Branch",
        on_delete=models.PROTECT,
        related_name="stock_movements",
    )
    product = models.ForeignKey(
        "products.Product",
        on_delete=models.PROTECT,
        related_name="stock_movements",
    )
    movement_type = models.CharField(
        max_length=30, choices=StockMovementType.choices,
    )
    delta = models.DecimalField(
        max_digits=14, decimal_places=4,
        help_text="Signed change: positive = inflow, negative = outflow.",
    )
    quantity_after = models.DecimalField(
        max_digits=14, decimal_places=4,
        help_text="Snapshot of the inventory balance after applying this movement.",
    )
    reason = models.TextField(
        blank=True,
        help_text="Required for adjustments, damaged, expired.",
    )
    reference = models.CharField(
        max_length=100, blank=True,
        help_text="Optional external reference, e.g. PO number or sale number.",
    )

    # Link to the sale if this movement came from a POS sale
    sale = models.ForeignKey(
        "sales.Sale",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="stock_movements",
    )

    performed_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        related_name="stock_movements",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["branch", "product", "-created_at"]),
            models.Index(fields=["movement_type"]),
            models.Index(fields=["branch", "created_at"]),
        ]

    def __str__(self) -> str:
        sign = "+" if self.delta >= 0 else ""
        return f"{sign}{self.delta} {self.product.sku} @ {self.branch.name} ({self.movement_type})"