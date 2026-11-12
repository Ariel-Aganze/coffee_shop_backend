from rest_framework import serializers

from .models import BusinessSettings, Currency, CurrencyChangeLog


class CurrencySerializer(serializers.ModelSerializer):
    class Meta:
        model = Currency
        fields = ("id", "code", "name", "symbol", "decimal_places", "country_hint")


class BusinessSettingsSerializer(serializers.ModelSerializer):
    # Read-only on purpose: the primary currency may only be changed through
    # POST /api/settings/me/change-currency/, which enforces owner-only access,
    # explicit confirmation, CurrencyChangeLog and audit (CUR-014/015/019).
    primary_currency = CurrencySerializer(read_only=True)

    class Meta:
        model = BusinessSettings
        fields = (
            "id",
            "primary_currency",
            "language",
            "timezone",
            "date_format",
            "number_format",
            "updated_at",
        )
        read_only_fields = ("id", "updated_at")


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