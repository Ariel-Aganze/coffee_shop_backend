from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import IsOwner
from apps.businesses.models import Business

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
    """
    permission_classes = [IsAuthenticated]

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

    def get_object(self):
        return BusinessSettings.objects.select_related(
            "primary_currency"
        ).get(business_id=self.request.user.business_id)

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

    @action(detail=False, methods=["get"], url_path="currency-history")
    def currency_history(self, request):
        qs = self.get_object().business.currency_changes.select_related(
            "old_currency", "new_currency", "changed_by"
        ).all()
        return Response(CurrencyChangeLogSerializer(qs, many=True).data)