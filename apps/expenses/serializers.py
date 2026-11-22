from decimal import Decimal

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from .models import Expense, ExpenseCategory

MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024
ATTACHMENT_TYPES = {"application/pdf", "image/png", "image/jpeg", "image/webp"}


def validate_attachment(value):
    """Receipts and supporting documents (EXP-007): PDF or image, 5 MB max."""
    if value is None:
        return value
    if value.size > MAX_ATTACHMENT_BYTES:
        raise serializers.ValidationError("The file must be 5 MB or smaller.")
    content_type = getattr(value, "content_type", None)
    if content_type and content_type not in ATTACHMENT_TYPES:
        raise serializers.ValidationError("Attach a PDF or a photo (PNG, JPEG or WebP).")
    return value


def _name(user):
    if user is None:
        return None
    return user.get_full_name() or user.username


class ExpenseCategorySerializer(serializers.ModelSerializer):
    expense_count = serializers.SerializerMethodField(help_text="Expenses filed under it.")

    class Meta:
        model = ExpenseCategory
        fields = ("id", "name", "description", "is_active", "expense_count", "created_at")
        read_only_fields = ("id", "created_at")

    @extend_schema_field(serializers.IntegerField())
    def get_expense_count(self, obj) -> int:
        if hasattr(obj, "num_expenses"):
            return obj.num_expenses
        return obj.expenses.count()

    def validate_name(self, value):
        value = value.strip()
        business_id = self.context["request"].user.business_id
        qs = ExpenseCategory.objects.filter(business_id=business_id, name__iexact=value)
        if self.instance is not None:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError("An expense category with this name already exists.")
        return value


class ExpenseSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source="category.name", read_only=True)
    branch_name = serializers.CharField(source="branch.name", read_only=True,
                                        allow_null=True, default=None)
    submitted_by_display = serializers.CharField(
        source="submitted_by.username", read_only=True
    )
    submitted_by_name = serializers.SerializerMethodField()
    reviewed_by_display = serializers.CharField(
        source="reviewed_by.username", read_only=True, allow_null=True, default=None,
    )
    reviewed_by_name = serializers.SerializerMethodField()
    attachment = serializers.SerializerMethodField()
    attachment_name = serializers.SerializerMethodField()

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
            "attachment_name",
            "status",
            "submitted_by",
            "submitted_by_display",
            "submitted_by_name",
            "reviewed_by",
            "reviewed_by_display",
            "reviewed_by_name",
            "reviewed_at",
            "review_note",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields

    def get_submitted_by_name(self, obj) -> str:
        return _name(obj.submitted_by)

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_reviewed_by_name(self, obj):
        return _name(obj.reviewed_by)

    @extend_schema_field(serializers.URLField(allow_null=True))
    def get_attachment(self, obj):
        """Receipts are private: this is the signed-in download endpoint, never
        a public /media/ URL."""
        if not obj.attachment:
            return None
        path = f"/api/expenses/{obj.pk}/attachment/"
        request = self.context.get("request")
        return request.build_absolute_uri(path) if request else path

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_attachment_name(self, obj):
        return obj.attachment.name.rsplit("/", 1)[-1] if obj.attachment else None


class ExpenseCreateSerializer(serializers.Serializer):
    category = serializers.IntegerField(min_value=1)
    amount = serializers.DecimalField(
        max_digits=14, decimal_places=4, min_value=Decimal("0.0001"),
    )
    description = serializers.CharField(max_length=2000)
    branch = serializers.IntegerField(min_value=1, required=False, allow_null=True)
    expense_date = serializers.DateField(required=False)
    attachment = serializers.FileField(required=False, allow_null=True,
                                       validators=[validate_attachment])


class ExpenseUpdateSerializer(serializers.Serializer):
    """Corrections while an expense is still pending (EXP-011: audited)."""
    category = serializers.IntegerField(min_value=1, required=False)
    amount = serializers.DecimalField(
        max_digits=14, decimal_places=4, min_value=Decimal("0.0001"), required=False,
    )
    description = serializers.CharField(max_length=2000, required=False)
    branch = serializers.IntegerField(min_value=1, required=False, allow_null=True)
    expense_date = serializers.DateField(required=False)
    attachment = serializers.FileField(required=False, allow_null=True,
                                       validators=[validate_attachment],
                                       help_text="null removes the file.")


class ExpenseReviewSerializer(serializers.Serializer):
    note = serializers.CharField(required=False, allow_blank=True, default="")


class ExpenseSummarySerializer(serializers.Serializer):
    """Totals for the expenses matching the list filters."""
    approved_total = serializers.DecimalField(max_digits=16, decimal_places=4)
    approved_count = serializers.IntegerField()
    pending_total = serializers.DecimalField(max_digits=16, decimal_places=4)
    pending_count = serializers.IntegerField()
    rejected_count = serializers.IntegerField()
