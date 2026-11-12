from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated

from apps.accounts.access import scope_to_branches
from apps.accounts.permissions import IsAdminOrOwner, IsOwner

from .models import Branch
from .serializers import BranchSerializer


class BranchViewSet(viewsets.ModelViewSet):
    serializer_class = BranchSerializer

    def get_permissions(self):
        if self.action in ("create", "destroy"):
            return [IsOwner()]
        if self.action in ("update", "partial_update"):
            # RBAC 4.2 "Manage branches": owner, or an authorized admin.
            return [IsAdminOrOwner()]
        # list / retrieve: any authenticated user, scoped by get_queryset
        return [IsAuthenticated()]

    def get_queryset(self):
        user = self.request.user
        qs = Branch.objects.filter(business_id=user.business_id)

        qs = scope_to_branches(qs, user, field="id")

        return qs.order_by("name")

    def perform_create(self, serializer):
        serializer.save(business=self.request.user.business)

    def perform_destroy(self, instance):
        instance.is_active = False
        instance.save(update_fields=["is_active"])