from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated

from apps.accounts.permissions import IsOwner, IsOwnerOrBranchManager

from .models import Branch
from .serializers import BranchSerializer


class BranchViewSet(viewsets.ModelViewSet):
    serializer_class = BranchSerializer

    def get_permissions(self):
        if self.action in ("create", "destroy"):
            return [IsOwner()]
        if self.action in ("update", "partial_update"):
            return [IsOwnerOrBranchManager()]
        # list / retrieve: any authenticated user, scoped by get_queryset
        return [IsAuthenticated()]

    def get_queryset(self):
        user = self.request.user
        qs = Branch.objects.filter(business_id=user.business_id)

        if user.role != "owner":
            qs = qs.filter(id__in=user.assigned_branch_ids)

        return qs.order_by("name")

    def perform_create(self, serializer):
        serializer.save(business=self.request.user.business)

    def perform_destroy(self, instance):
        instance.is_active = False
        instance.save(update_fields=["is_active"])