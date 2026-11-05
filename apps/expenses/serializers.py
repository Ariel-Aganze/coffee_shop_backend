from decimal import Decimal

from rest_framework import serializers

from .models import Expense, ExpenseCategory


class ExpenseCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = ExpenseCategory
        fields = ("id", "name", "description", "is_active", "created_at")
        read_only_fields = ("id", "created_at")


class ExpenseSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source="category.name", read_only=True)
    branch_name = serializers.CharField(source="branch.name", read_only=True)
    submitted_by_display = serializers.CharField(
        source="submitted_by.username", read_only=True
    )
    reviewed_by_display = serializers.CharField(
        source="reviewed_by.username", read_only=True
    )

    class Meta:
        model = Expense
        fields = (
            "id",
            "business",
            "branch",
            "branch_name",
            "category",
            "category_name",
            "amount",
            "currency_code",
            "currency_decimal_places",
            "description",
            "expense_date",
            "attachment",
            "status",
            "submitted_by",
            "submitted_by_display",
            "reviewed_by",
            "reviewed_by_display",
            "reviewed_at",
            "review_note",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields


class ExpenseCreateSerializer(serializers.Serializer):
    category = serializers.IntegerField(min_value=1)
    amount = serializers.DecimalField(
        max_digits=14, decimal_places=4, min_value=Decimal("0.0001"),
    )
    description = serializers.CharField()
    branch = serializers.IntegerField(min_value=1, required=False, allow_null=True)
    expense_date = serializers.DateField(required=False)
    attachment = serializers.FileField(required=False, allow_null=True)


class ExpenseReviewSerializer(serializers.Serializer):
    note = serializers.CharField(required=False, allow_blank=True, default="")