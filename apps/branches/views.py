from django.db.models import Count, Prefetch, Q
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import viewsets
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated

from apps.accounts.access import scope_to_branches
from apps.accounts.models import UserBranch
from apps.accounts.permissions import IsAdminOrOwner, IsOwner
from apps.audit.services import log_action

from .models import Branch
from .serializers import BranchSerializer


@extend_schema_view(list=extend_schema(parameters=[
    OpenApiParameter("search", str, description="Name, address or phone."),
    OpenApiParameter("is_active", bool, description="true = active, false = deactivated."),
]))
class BranchViewSet(viewsets.ModelViewSet):
    """
    Branches of the signed-in user's business (BR-001..BR-012), scoped to the
    branches they may see. Branches are deactivated, never deleted (BR-012).
    """
    serializer_class = BranchSerializer
    queryset = Branch.objects.none()  # schema only; real queryset in get_queryset

    def get_permissions(self):
        if self.action in ("create", "destroy"):
            return [IsOwner()]
        if self.action in ("update", "partial_update"):
            # RBAC 4.2 "Manage branches": owner, or an authorized admin.
            return [IsAdminOrOwner()]
        # list / retrieve: any authenticated user, scoped by get_queryset
        return [IsAuthenticated()]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Branch.objects.none()
        user = self.request.user
        qs = scope_to_branches(Branch.objects.filter(business_id=user.business_id), user, field="id")
        qs = qs.annotate(
            active_staff_count=Count(
                "user_assignments", filter=Q(user_assignments__user__is_active=True), distinct=True,
            ),
        ).prefetch_related(
            Prefetch(
                "user_assignments",
                queryset=UserBranch.objects.filter(
                    user__role="branch_manager", user__is_active=True,
                ).select_related("user"),
                to_attr="manager_assignments",
            )
        )
        if self.action == "list":
            params = self.request.query_params
            if search := params.get("search", "").strip():
                qs = qs.filter(
                    Q(name__icontains=search) | Q(address__icontains=search)
                    | Q(phone__icontains=search)
                )
            if (active := params.get("is_active")) in ("true", "false"):
                qs = qs.filter(is_active=active == "true")
        return qs.order_by("-is_active", "name")

    def perform_create(self, serializer):
        branch = serializer.save(business=self.request.user.business)
        log_action(business=branch.business, branch=branch, user=self.request.user,
                   action="branch_created", target_type="Branch", target_id=str(branch.pk),
                   metadata={"name": branch.name})

    def perform_update(self, serializer):
        before = {"name": serializer.instance.name, "is_active": serializer.instance.is_active}
        branch = serializer.save()
        log_action(business=branch.business, branch=branch, user=self.request.user,
                   action="branch_updated", target_type="Branch", target_id=str(branch.pk),
                   previous_value=before, new_value={"name": branch.name, "is_active": branch.is_active})

    def perform_destroy(self, instance):
        from apps.cash_management.models import CashSession, CashSessionStatus

        open_sessions = CashSession.objects.filter(branch=instance, status=CashSessionStatus.OPEN).count()
        if open_sessions:
            raise ValidationError(
                f"{open_sessions} cash session(s) are still open at this branch. "
                "Close them before deactivating it."
            )
        instance.is_active = False
        instance.save(update_fields=["is_active"])
        log_action(business=instance.business, branch=instance, user=self.request.user,
                   action="branch_deactivated", target_type="Branch", target_id=str(instance.pk),
                   metadata={"name": instance.name})
