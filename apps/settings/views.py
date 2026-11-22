from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import IsAdminOrOwner
from apps.audit.services import log_action
from drf_spectacular.utils import OpenApiParameter, extend_schema

from config.schema import ErrorSerializer

from .models import BusinessSettings, Currency
from .serializers import (
    BusinessSettingsSerializer,
    CurrencyChangeLogSerializer,
    CurrencyChangeRequestSerializer,
    CurrencySerializer,
)
from .services import change_primary_currency


class CurrencyListView(APIView):
    """
    Public read-only currency catalogue (CUR-004..CUR-007).
    Supports search by name / ISO code / country.

    Public (no sign-in): the registration wizard needs it before an account
    exists, and it only contains ISO 4217 reference data.
    """
    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(
        parameters=[OpenApiParameter("search", str, description="Search by ISO code, name or country.")],
        responses=CurrencySerializer(many=True),
    )
    def get(self, request):
        qs = Currency.objects.filter(is_active=True)
        search = request.query_params.get("search", "").strip()
        if search:
            qs = qs.filter(
                code__icontains=search
            ) | qs.filter(
                name__icontains=search
            ) | qs.filter(
                country_hint__icontains=search
            )
        qs = qs.order_by("code").distinct()
        return Response(CurrencySerializer(qs, many=True).data)


class BusinessSettingsViewSet(viewsets.GenericViewSet,
                              mixins.RetrieveModelMixin,
                              mixins.UpdateModelMixin):
    """
    /api/settings/me/       GET
    /api/settings/me/       PATCH   (language, timezone, date_format, number_format)
    /api/settings/me/change-currency/   POST
    /api/settings/me/currency-history/  GET
    """
    serializer_class = BusinessSettingsSerializer
    permission_classes = [IsAuthenticated]
    queryset = BusinessSettings.objects.none()  # schema only; see get_object

    def get_permissions(self):
        # RBAC 4.2: only the owner (or an authorized admin) manages settings.
        if self.action in ("update", "partial_update"):
            return [IsAdminOrOwner()]
        return [IsAuthenticated()]

    def perform_update(self, serializer):
        def jsonable(value):
            # Audit values are stored as JSON: keep str/int/bool/None as-is,
            # stringify everything else (e.g. Decimal).
            return value if isinstance(value, (str, int, bool, type(None))) else str(value)

        fields = list(serializer.validated_data)
        before = {f: jsonable(getattr(serializer.instance, f)) for f in fields}
        settings = serializer.save()
        after = {f: jsonable(getattr(settings, f)) for f in fields}
        log_action(
            business=settings.business,
            user=self.request.user,
            action="business_settings_updated",
            target_type="BusinessSettings",
            target_id=str(settings.pk),
            previous_value=before,
            new_value=after,
        )

    def get_object(self):
        # Always the requester's own business settings (singleton "me").
        from django.shortcuts import get_object_or_404

        return get_object_or_404(
            BusinessSettings.objects.select_related("primary_currency"),
            business_id=self.request.user.business_id,
        )

    @extend_schema(request=CurrencyChangeRequestSerializer, responses={200: BusinessSettingsSerializer, 400: ErrorSerializer, 403: ErrorSerializer, 404: ErrorSerializer})
    @action(detail=False, methods=["post"], url_path="change-currency")
    def change_currency(self, request):
        if request.user.role != "owner":
            return Response(
                {"error": "Only the business owner can change the primary currency."},
                status=status.HTTP_403_FORBIDDEN,
            )

        payload = CurrencyChangeRequestSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        if not payload.validated_data["confirm"]:
            return Response(
                {"error": "You must confirm the change by setting `confirm=true`."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            currency = Currency.objects.get(
                code=payload.validated_data["currency_code"].upper(),
                is_active=True,
            )
        except Currency.DoesNotExist:
            return Response(
                {"error": "Currency not found or inactive."},
                status=status.HTTP_404_NOT_FOUND,
            )

        settings = change_primary_currency(
            settings=self.get_object(),
            new_currency=currency,
            user=request.user,
            reason=payload.validated_data.get("reason", ""),
        )

        return Response(BusinessSettingsSerializer(settings).data)

    @extend_schema(responses=CurrencyChangeLogSerializer(many=True))
    # A handful of rows at most: returned whole (not paginated).
    @action(detail=False, methods=["get"], url_path="currency-history", pagination_class=None)
    def currency_history(self, request):
        qs = self.get_object().business.currency_changes.select_related(
            "old_currency", "new_currency", "changed_by"
        ).all()
        return Response(CurrencyChangeLogSerializer(qs, many=True).data)