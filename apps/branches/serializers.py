from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from .models import Branch


class BranchManagerSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()


class BranchSerializer(serializers.ModelSerializer):
    staff_count = serializers.SerializerMethodField(
        help_text="Active staff assigned to this branch.")
    managers = serializers.SerializerMethodField(
        help_text="Active branch managers assigned to this branch (BR-006).")

    class Meta:
        model = Branch
        fields = (
            "id",
            "business",
            "name",
            "address",
            "phone",
            "is_active",
            "staff_count",
            "managers",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "business", "created_at", "updated_at")

    def validate_name(self, value):
        # unique_together(business, name) isn't checked by DRF because
        # `business` is set server-side: check here (any letter case), so a
        # duplicate is a clear 400 instead of a database error.
        value = value.strip()
        request = self.context.get("request")
        business_id = getattr(getattr(request, "user", None), "business_id", None)
        qs = Branch.objects.filter(business_id=business_id, name__iexact=value)
        if self.instance is not None:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError("A branch with this name already exists.")
        return value

    @extend_schema_field(serializers.IntegerField())
    def get_staff_count(self, obj):
        # Annotated by the list view; computed for single objects.
        if hasattr(obj, "active_staff_count"):
            return obj.active_staff_count
        return obj.user_assignments.filter(user__is_active=True).count()

    @extend_schema_field(BranchManagerSerializer(many=True))
    def get_managers(self, obj):
        assignments = getattr(obj, "manager_assignments", None)
        if assignments is None:
            assignments = obj.user_assignments.filter(
                user__role="branch_manager", user__is_active=True,
            ).select_related("user")
        return [
            {"id": a.user_id, "name": a.user.get_full_name() or a.user.username}
            for a in assignments
        ]
