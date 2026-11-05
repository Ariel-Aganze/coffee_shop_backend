from rest_framework import status, viewsets
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit.services import log_action
from apps.branches.serializers import BranchSerializer
from apps.settings.serializers import BusinessSettingsSerializer

from .models import Business
from .serializers import BusinessRegistrationSerializer, BusinessSerializer


class BusinessRegistrationView(APIView):
    """
    Public onboarding endpoint (workflow 12.1).
    POST /api/businesses/register/
    """
    permission_classes = [AllowAny]
    authentication_classes = []

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

    def get_queryset(self):
        return Business.objects.filter(id=self.request.user.business_id)