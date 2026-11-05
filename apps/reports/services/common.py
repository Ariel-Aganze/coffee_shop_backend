from datetime import date, datetime, time, timedelta

from django.utils import timezone
from rest_framework.exceptions import ValidationError


def parse_date(value: str | None, field_name: str = "date") -> date | None:
    if value is None or value == "":
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        raise ValidationError(f"Invalid {field_name} format. Use YYYY-MM-DD.")


def parse_range(request) -> tuple[date, date]:
    """
    Returns (from_date, to_date). If neither is provided, defaults to today.
    If only `from` is provided, `to` defaults to today.
    If only `to` is provided, `from` defaults to 30 days before `to`.
    """
    today = timezone.localdate()
    f = parse_date(request.query_params.get("from"), "from")
    t = parse_date(request.query_params.get("to"), "to")

    if f is None and t is None:
        f = t = today
    elif f is None:
        f = t - timedelta(days=30)
    elif t is None:
        t = today

    if f > t:
        raise ValidationError("`from` must be on or before `to`.")
    return f, t


def start_of_day(d: date) -> datetime:
    return timezone.make_aware(datetime.combine(d, time.min))


def end_of_day(d: date) -> datetime:
    return timezone.make_aware(datetime.combine(d, time.max))


def branch_scope(request):
    """
    Returns a Q-like filter dict for branch scoping based on role.
    Owners have no branch restriction.
    """
    user = request.user
    if user.role == "owner":
        return None
    return user.assigned_branch_ids