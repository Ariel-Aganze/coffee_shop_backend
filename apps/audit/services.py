from .models import AuditLog


def log_action(
    *,
    business=None,
    branch=None,
    user=None,
    action: str,
    target_type: str = "",
    target_id: str = "",
    metadata: dict | None = None,
    previous_value: dict | None = None,
    new_value: dict | None = None,
) -> AuditLog:
    """
    Centralized audit log writer. Use from any service.
    """
    return AuditLog.objects.create(
        business=business,
        branch=branch,
        user=user,
        action=action,
        target_type=target_type,
        target_id=target_id,
        metadata=metadata or {},
        previous_value=previous_value,
        new_value=new_value,
    )