from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.audit.services import log_action

from .models import BusinessSettings, Currency, CurrencyChangeLog


@transaction.atomic
def change_primary_currency(*, settings: BusinessSettings, new_currency: Currency,
                             user, reason: str = "") -> BusinessSettings:
    """
    Change the primary currency for a business while preserving history.
    Returns the updated BusinessSettings.
    """
    old_currency = settings.primary_currency

    if old_currency.code == new_currency.code:
        raise ValidationError("The business is already using this currency.")

    if not new_currency.is_active:
        raise ValidationError("This currency is not available.")

    now = timezone.now()

    settings.primary_currency = new_currency
    settings.save(update_fields=["primary_currency", "updated_at"])

    log = CurrencyChangeLog.objects.create(
        business=settings.business,
        old_currency=old_currency,
        new_currency=new_currency,
        changed_by=user,
        effective_date=now,
        reason=reason,
    )

    log_action(
        business=settings.business,
        user=user,
        action="currency_changed",
        target_type="BusinessSettings",
        target_id=str(settings.pk),
        metadata={
            "old_currency": old_currency.code,
            "new_currency": new_currency.code,
            "change_log_id": str(log.pk),
        },
    )

    return settings