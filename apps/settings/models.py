from django.core.validators import MinValueValidator
from django.db import models


class Currency(models.Model):
    """
    Currency catalogue entry (ISO 4217).
    """
    code = models.CharField(max_length=3, unique=True)
    name = models.CharField(max_length=100)
    symbol = models.CharField(max_length=10, blank=True)
    decimal_places = models.PositiveSmallIntegerField(
        default=2,
        validators=[MinValueValidator(0)],
        help_text="Minor-unit precision (e.g., RWF = 0, USD = 2, LYD = 3).",
    )
    country_hint = models.CharField(
        max_length=255, blank=True,
        help_text="Informational only (e.g., 'Rwanda').",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]
        verbose_name_plural = "Currencies"

    def __str__(self) -> str:
        return f"{self.code} - {self.name}"


class BusinessSettings(models.Model):
    """
    Per-business configuration: primary currency and regional settings.
    One-to-one with Business.
    """
    business = models.OneToOneField(
        "businesses.Business",
        on_delete=models.CASCADE,
        related_name="settings",
    )
    primary_currency = models.ForeignKey(
        Currency,
        on_delete=models.PROTECT,
        related_name="businesses_using_as_primary",
    )
    language = models.CharField(max_length=10, default="en")
    timezone = models.CharField(max_length=64, default="UTC")
    date_format = models.CharField(max_length=20, default="YYYY-MM-DD")
    number_format = models.CharField(max_length=20, default="1,234.56")
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"Settings of {self.business.name}"


class CurrencyChangeLog(models.Model):
    """
    Audit of primary-currency changes (CUR-019).
    """
    business = models.ForeignKey(
        "businesses.Business",
        on_delete=models.CASCADE,
        related_name="currency_changes",
    )
    old_currency = models.ForeignKey(
        Currency,
        on_delete=models.PROTECT,
        related_name="+",
    )
    new_currency = models.ForeignKey(
        Currency,
        on_delete=models.PROTECT,
        related_name="+",
    )
    changed_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        related_name="+",
    )
    changed_at = models.DateTimeField(auto_now_add=True)
    effective_date = models.DateTimeField(
        help_text="Moment from which the new currency applies (usually now).",
    )
    reason = models.TextField(blank=True)

    class Meta:
        ordering = ["-changed_at"]

    def __str__(self) -> str:
        return f"{self.business.name}: {self.old_currency.code} -> {self.new_currency.code}"