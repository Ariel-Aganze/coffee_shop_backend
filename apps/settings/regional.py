"""
Allowed regional preferences (SRS 5.14.1, NFR-039..041), shared by business
registration and the settings endpoint so both validate the same way.
"""
from zoneinfo import available_timezones

from rest_framework import serializers

LANGUAGE_CHOICES = [
    ("en", "English"),
    ("fr", "Français"),
]

DATE_FORMAT_CHOICES = [
    ("YYYY-MM-DD", "2026-03-04"),
    ("DD/MM/YYYY", "04/03/2026"),
    ("MM/DD/YYYY", "03/04/2026"),
    ("DD MMM YYYY", "04 Mar 2026"),
]

NUMBER_FORMAT_CHOICES = [
    ("1,234.56", "1,234.56"),
    ("1.234,56", "1.234,56"),
    ("1 234,56", "1 234,56"),
    ("1'234.56", "1'234.56"),
]

_TIMEZONES = available_timezones() | {"UTC"}


def validate_timezone(value: str) -> str:
    if value not in _TIMEZONES:
        raise serializers.ValidationError("Unknown timezone. Use an IANA name such as Africa/Kigali.")
    return value


# ------------------------------------------------------------------------------
# Business-local days. A "day" filter (?from=2026-11-13) means that calendar
# day AT THE SHOP, not in UTC: a sale at 01:00 in Kigali is 23:00 UTC the day
# before.
# ------------------------------------------------------------------------------
def business_zone(business_id):
    from datetime import timezone as dt_timezone
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    from .models import BusinessSettings

    name = (
        BusinessSettings.objects.filter(business_id=business_id)
        .values_list("timezone", flat=True).first()
    ) or "UTC"
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return dt_timezone.utc


def parse_day(value, name):
    from datetime import datetime

    from rest_framework.exceptions import ValidationError

    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        raise ValidationError({name: "Invalid date. Use YYYY-MM-DD."})


def day_start(value, name, tz):
    from datetime import datetime, time

    return datetime.combine(parse_day(value, name), time.min, tzinfo=tz)


def day_end(value, name, tz):
    from datetime import datetime, time

    return datetime.combine(parse_day(value, name), time.max, tzinfo=tz)
