"""
Audit trail API (AUD-009, AUD-010; RBAC 4.2 "View audit logs").

    GET /api/audit/               search the audit trail (paginated, newest first)
    GET /api/audit/{id}/          one record, with previous / new values
    GET /api/audit/actions/       distinct action names visible to you (for filters)

Filters (all optional, combinable):
    action       exact action name; comma-separate several (action=sale_refunded,sale_cancelled)
    user         id of the user who performed the action
    branch       branch id (403 if it's not one of yours)
    target_type  e.g. Sale, Expense, User
    target_id    id of the affected record (use with target_type for a record's history)
    from, to     YYYY-MM-DD, inclusive, in the business timezone
    search       free text across action, target, username and metadata

Read-only by design: there are no write endpoints, and the model itself
refuses updates and deletes.
"""
from datetime import datetime, time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.db.models import Q
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from apps.accounts.access import accessible_branch_ids, requested_branch_scope
from apps.accounts.constants import UserRole
from apps.accounts.permissions import HasAnyRole
from apps.settings.models import BusinessSettings

from .models import AuditLog
from .serializers import AuditLogSerializer

# Inventory managers' view is "Limited" to stock activity at their branches.
INVENTORY_ACTIONS = ("stock_movement",)


class AuditLogViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin,
                      viewsets.GenericViewSet):
    serializer_class = AuditLogSerializer
    queryset = AuditLog.objects.none()  # real queryset built per request in get_queryset
    permission_classes = [HasAnyRole]
    # RBAC 4.2: cashiers have no access; IM and branch managers are "Limited"
    # (their branches only); owner, admin and accountant are "Authorized".
    allowed_roles = (
        UserRole.OWNER, UserRole.ADMIN, UserRole.ACCOUNTANT,
        UserRole.BRANCH_MANAGER, UserRole.INVENTORY_MANAGER,
    )

    # ---------- Visibility ----------
    def _visible(self):
        user = self.request.user
        qs = AuditLog.objects.filter(business_id=user.business_id)
        scope = accessible_branch_ids(user)
        if scope is not None:
            # Branch-scoped users only see activity at their branches;
            # business-level records (settings, currency, accounts) are hidden.
            qs = qs.filter(branch_id__in=scope)
        if user.role == UserRole.INVENTORY_MANAGER:
            qs = qs.filter(action__in=INVENTORY_ACTIONS)
        return qs

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):  # OpenAPI schema generation
            return AuditLog.objects.none()
        qs = self._visible().select_related("user", "branch")
        if self.action != "list":
            return qs

        params = self.request.query_params
        if params.get("action"):
            qs = qs.filter(action__in=[a.strip() for a in params["action"].split(",") if a.strip()])
        if params.get("user"):
            qs = qs.filter(user_id=_int(params["user"], "user"))
        if params.get("branch"):
            qs = qs.filter(branch_id__in=requested_branch_scope(self.request))
        if params.get("target_type"):
            qs = qs.filter(target_type__iexact=params["target_type"])
        if params.get("target_id"):
            qs = qs.filter(target_id=params["target_id"])

        tz = self._business_tz()
        if params.get("from"):
            qs = qs.filter(created_at__gte=_day_start(params["from"], "from", tz))
        if params.get("to"):
            qs = qs.filter(created_at__lte=_day_end(params["to"], "to", tz))

        search = params.get("search", "").strip()
        if search:
            qs = qs.filter(
                Q(action__icontains=search)
                | Q(target_type__icontains=search)
                | Q(target_id__icontains=search)
                | Q(user__username__icontains=search)
                | Q(metadata__icontains=search)
            )
        return qs.order_by("-created_at", "-id")

    @extend_schema(responses={200: {"type": "array", "items": {"type": "string"}}})
    @action(detail=False, methods=["get"], url_path="actions")
    def actions(self, request):
        names = self._visible().order_by("action").values_list("action", flat=True).distinct()
        return Response(list(names))

    def _business_tz(self):
        name = (
            BusinessSettings.objects.filter(business_id=self.request.user.business_id)
            .values_list("timezone", flat=True).first()
        ) or "UTC"
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError):
            return ZoneInfo("UTC")


def _int(value, name):
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValidationError(f"`{name}` must be an id.")


def _date(value, name):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        raise ValidationError(f"Invalid `{name}` date. Use YYYY-MM-DD.")


def _day_start(value, name, tz):
    return datetime.combine(_date(value, name), time.min, tzinfo=tz)


def _day_end(value, name, tz):
    return datetime.combine(_date(value, name), time.max, tzinfo=tz)
