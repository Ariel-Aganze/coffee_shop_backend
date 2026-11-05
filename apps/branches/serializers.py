from rest_framework import serializers

from .models import Branch


class BranchSerializer(serializers.ModelSerializer):
    class Meta:
        model = Branch
        fields = (
            "id",
            "business",
            "name",
            "address",
            "phone",
            "is_active",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "business", "created_at", "updated_at")