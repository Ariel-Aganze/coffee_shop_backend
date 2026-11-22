from rest_framework import serializers

from .models import Notification


class NotificationSerializer(serializers.ModelSerializer):
    branch_name = serializers.CharField(source="branch.name", read_only=True,
                                        allow_null=True, default=None)

    class Meta:
        model = Notification
        fields = (
            "id",
            "notification_type",
            "title",
            "message",
            "metadata",
            "branch",
            "branch_name",
            "is_read",
            "read_at",
            "created_at",
        )
        read_only_fields = fields