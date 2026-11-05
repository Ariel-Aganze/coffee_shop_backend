from django.contrib import admin

from .models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("action", "user", "business", "branch", "target_type", "created_at")
    list_filter = ("action", "business")
    search_fields = ("action", "target_type", "target_id")
    readonly_fields = (
        "business", "branch", "user", "action", "target_type",
        "target_id", "metadata", "previous_value", "new_value", "created_at",
    )