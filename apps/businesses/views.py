from rest_framework import status, viewsets
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import IsAdminOrOwner
from apps.audit.services import log_action
from apps.branches.serializers import BranchSerializer
from apps.settings.serializers import BusinessSettingsSerializer
from drf_spectacular.utils import extend_schema

from .models import Business
from .serializers import (
    BusinessRegistrationSerializer,
    BusinessSerializer,
    RegistrationResponseSerializer,
)


class BusinessRegistrationView(APIView):
    """
    Public onboarding endpoint (workflow 12.1).
    POST /api/businesses/register/
    """
    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(request=BusinessRegistrationSerializer, responses={201: RegistrationResponseSerializer}, auth=[])
    def post(self, request):
        serializer = BusinessRegistrationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = serializer.save()

        business = result["business"]
        branch = result["branch"]
        settings = result["settings"]

        log_action(
            business=business,
            branch=branch,
            user=result["owner"],
            action="business_registered",
            target_type="Business",
            target_id=str(business.pk),
            metadata={"currency": settings.primary_currency.code},
        )

        return Response(
            {
                "business": BusinessSerializer(business).data,
                "branch": BranchSerializer(branch).data,
                "settings": BusinessSettingsSerializer(settings).data,
            },
            status=status.HTTP_201_CREATED,
        )


class BusinessViewSet(viewsets.ModelViewSet):
    """
    Owner can view/update their own business.
    """
    serializer_class = BusinessSerializer
    permission_classes = [IsAuthenticated]
    http_method_names = ["get", "patch", "head", "options"]

    def get_permissions(self):
        # RBAC 4.2: only the owner (or an authorized admin) edits the business profile.
        if self.action in ("update", "partial_update"):
            return [IsAdminOrOwner()]
        return [IsAuthenticated()]

    def get_queryset(self):
        return Business.objects.filter(id=self.request.user.business_id)

    def get_object(self):
        # GET/PATCH /api/businesses/me/ — always the requester's own business.
        from django.shortcuts import get_object_or_404

        obj = get_object_or_404(self.get_queryset())
        self.check_object_permissions(self.request, obj)
        return obj

    def perform_update(self, serializer):
        business = serializer.save()
        log_action(
            business=business,
            user=self.request.user,
            action="business_profile_updated",
            target_type="Business",
            target_id=str(business.pk),
            metadata={"fields": sorted(serializer.validated_data.keys())},
        )