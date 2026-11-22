from rest_framework import serializers

from apps.payments.models import PaymentMethod

from .models import BusinessSettings, Currency, CurrencyChangeLog
from .regional import (
    DATE_FORMAT_CHOICES,
    LANGUAGE_CHOICES,
    NUMBER_FORMAT_CHOICES,
    validate_timezone,
)


class CurrencySerializer(serializers.ModelSerializer):
    class Meta:
        model = Currency
        fields = ("id", "code", "name", "symbol", "decimal_places", "country_hint")


class BusinessSettingsSerializer(serializers.ModelSerializer):
    # Read-only on purpose: the primary currency may only be changed through
    # POST /api/settings/me/change-currency/, which enforces owner-only access,
    # explicit confirmation, CurrencyChangeLog and audit (CUR-014/015/019).
    primary_currency = CurrencySerializer(read_only=True)
    language = serializers.ChoiceField(choices=LANGUAGE_CHOICES)
    timezone = serializers.CharField(max_length=64, validators=[validate_timezone])
    date_format = serializers.ChoiceField(choices=DATE_FORMAT_CHOICES)
    number_format = serializers.ChoiceField(choices=NUMBER_FORMAT_CHOICES)
    enabled_payment_methods = serializers.ListField(
        child=serializers.ChoiceField(choices=PaymentMethod.choices),
        allow_empty=False,
        help_text="Payment methods offered at the point of sale.",
    )

    class Meta:
        model = BusinessSettings
        fields = (
            "id",
            "primary_currency",
            "language",
            "timezone",
            "date_format",
            "number_format",
            "cashier_max_discount_percent",
            "enabled_payment_methods",
            "updated_at",
        )
        read_only_fields = ("id", "updated_at")

    def validate_enabled_payment_methods(self, value):
        # Keep the canonical order and drop duplicates.
        return [m for m in PaymentMethod.values if m in set(value)]


class CurrencyChangeRequestSerializer(serializers.Serializer):
    """
    Payload for changing a business's primary currency.
    """
    currency_code = serializers.CharField(max_length=3)
    confirm = serializers.BooleanField(
        help_text="Must be true to confirm the change (CUR-015).",
    )
    reason = serializers.CharField(required=False, allow_blank=True, default="")


class CurrencyChangeLogSerializer(serializers.ModelSerializer):
    old_currency = CurrencySerializer(read_only=True)
    new_currency = CurrencySerializer(read_only=True)

    class Meta:
        model = CurrencyChangeLog
        fields = (
            "id",
            "old_currency",
            "new_currency",
            "changed_by",
            "changed_at",
            "effective_date",
            "reason",
        )
        read_only_fields = fields