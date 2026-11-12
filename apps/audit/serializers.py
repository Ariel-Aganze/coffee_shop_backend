from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from .models import AuditLog


class AuditUserSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    username = serializers.CharField()
    name = serializers.CharField()
    role = serializers.CharField()


class AuditBranchSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()


class AuditLogSerializer(serializers.ModelSerializer):
    user = serializers.SerializerMethodField()
    branch = serializers.SerializerMethodField()

    class Meta:
        model = AuditLog
        fields = (
            "id", "created_at", "action", "user", "branch",
            "target_type", "target_id", "metadata",
            "previous_value", "new_value",
        )
        read_only_fields = fields

    @extend_schema_field(AuditUserSerializer(allow_null=True))
    def get_user(self, obj):
        if obj.user is None:
            return None
        return {
            "id": obj.user_id,
            "username": obj.user.username,
            "name": obj.user.get_full_name() or obj.user.username,
            "role": obj.user.role,
        }

    @extend_schema_field(AuditBranchSerializer(allow_null=True))
    def get_branch(self, obj):
        if obj.branch is None:
            return None
        return {"id": obj.branch_id, "name": obj.branch.name}
