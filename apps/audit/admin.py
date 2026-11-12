from django.contrib import admin

from .models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    """Read-only: audit records can be inspected but never added, edited or
    deleted, even by superusers (AUD-010)."""
    list_display = ("action", "user", "business", "branch", "target_type", "created_at")
    list_filter = ("action", "business")
    search_fields = ("action", "target_type", "target_id")
    readonly_fields = (
        "business", "branch", "user", "action", "target_type",
        "target_id", "metadata", "previous_value", "new_value", "created_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
