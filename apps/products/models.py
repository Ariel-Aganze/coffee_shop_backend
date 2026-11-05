from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone


class Category(models.Model):
    """
    Product category (e.g., "Hot Drinks", "Pastries").
    Each category belongs to one business.
    """
    business = models.ForeignKey(
        "businesses.Business",
        on_delete=models.CASCADE,
        related_name="categories",
    )
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("business", "name")
        ordering = ["name"]
        verbose_name_plural = "Categories"

    def __str__(self) -> str:
        return self.name


class ProductType(models.TextChoices):
    DRINK = "drink", "Prepared Drink"
    FOOD = "food", "Food Item"
    PACKAGED = "packaged", "Packaged Product"


class Product(models.Model):
    """
    A product sold by the business.
    SKU is unique per business (PROD-003).
    """
    business = models.ForeignKey(
        "businesses.Business",
        on_delete=models.CASCADE,
        related_name="products",
    )
    category = models.ForeignKey(
        Category,
        on_delete=models.PROTECT,
        related_name="products",
        null=True,
        blank=True,
    )
    name = models.CharField(max_length=255)
    sku = models.CharField(
        max_length=64,
        help_text="Unique identifier within the business.",
    )
    description = models.TextField(blank=True)
    image = models.ImageField(upload_to="products/", blank=True, null=True)
    product_type = models.CharField(
        max_length=20,
        choices=ProductType.choices,
        default=ProductType.DRINK,
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("business", "sku")
        ordering = ["name"]
        indexes = [
            models.Index(fields=["business", "is_active"]),
            models.Index(fields=["business", "category"]),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.sku})"

    # ---------- Price helpers ----------

    def current_price(self, at: timezone.datetime | None = None):
        """
        Return the currently effective ProductPrice row for this product,
        or None. Historical rows are preserved for reporting.
        """
        now = at or timezone.now()
        return self.prices.filter(effective_from__lte=now).order_by(
            "-effective_from"
        ).first()

    def current_selling_price(self, at: timezone.datetime | None = None) -> Decimal | None:
        price_row = self.current_price(at)
        return price_row.selling_price if price_row else None


class ProductPrice(models.Model):
    """
    Historical pricing (PROD-009, BRULE-006).
    Never update an existing row — always create a new one.
    The product's 'active' price at any time is the row with the
    most recent `effective_from <= now`.
    """
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="prices",
    )
    selling_price = models.DecimalField(
        max_digits=14,
        decimal_places=4,
        validators=[MinValueValidator(Decimal("0"))],
    )
    cost_price = models.DecimalField(
        max_digits=14,
        decimal_places=4,
        validators=[MinValueValidator(Decimal("0"))],
        default=Decimal("0"),
        help_text="Cost of goods for this product (optional).",
    )
    effective_from = models.DateTimeField(default=timezone.now)
    created_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-effective_from"]
        indexes = [
            models.Index(fields=["product", "effective_from"]),
        ]

    def __str__(self) -> str:
        return f"{self.product.sku} @ {self.selling_price} from {self.effective_from:%Y-%m-%d}"


class BranchProductAvailability(models.Model):
    """
    Per-branch availability (PROD-008).
    If no row exists for a (branch, product) pair, the product is considered
    available at that branch by default. A row with `is_available=False`
    explicitly hides the product from that branch.
    """
    branch = models.ForeignKey(
        "branches.Branch",
        on_delete=models.CASCADE,
        related_name="product_availabilities",
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="branch_availabilities",
    )
    is_available = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("branch", "product")
        indexes = [
            models.Index(fields=["branch", "product"]),
        ]

    def __str__(self) -> str:
        return f"{self.product.sku} @ {self.branch.name}: {self.is_available}"