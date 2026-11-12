"""
Branch-level data access rules (SRS 4.2 / 4.3, BRULE-003).

Every view and service that scopes data by branch goes through these
helpers, so the rules live in one place:

  - Owner: every branch of their business.
  - Accountant: every branch of the business by default ("View all
    branches: Authorized"); if they are assigned to specific branches,
    only those.
  - Everyone else (admin, branch manager, cashier, inventory manager):
    only the branches they are assigned to. A system administrator does
    NOT get unrestricted access to financial records (SRS 4.1.2).
"""
from rest_framework.exceptions import PermissionDenied, ValidationError

from .constants import UserRole


def accessible_branch_ids(user) -> list[int] | None:
    """Branch ids the user may see, or None meaning "all of their business"."""
    if user.role == UserRole.OWNER:
        return None
    assigned = user.assigned_branch_ids
    if user.role == UserRole.ACCOUNTANT and not assigned:
        return None
    return assigned


def has_business_wide_access(user) -> bool:
    return accessible_branch_ids(user) is None


def can_access_branch(user, branch) -> bool:
    if branch.business_id != user.business_id:
        return False
    allowed = accessible_branch_ids(user)
    return allowed is None or branch.id in allowed


def scope_to_branches(qs, user, field: str = "branch_id"):
    """Filter a queryset to the branches the user may see."""
    allowed = accessible_branch_ids(user)
    if allowed is None:
        return qs
    return qs.filter(**{f"{field}__in": allowed})


def requested_branch_scope(request) -> list[int] | None:
    """
    Branch scope for a request, honouring an optional `?branch=<id>` filter
    (DASH-012). Returns None for "all branches of the business", otherwise a
    list of branch ids. Asking for a branch you can't see is a 403, not an
    empty result, so the frontend can tell the two apart.
    """
    from apps.branches.models import Branch

    user = request.user
    allowed = accessible_branch_ids(user)
    raw = request.query_params.get("branch")
    if not raw:
        return allowed
    try:
        branch_id = int(raw)
    except (TypeError, ValueError):
        raise ValidationError("`branch` must be a branch id.")
    branch = Branch.objects.filter(pk=branch_id, business_id=user.business_id).first()
    if branch is None or not can_access_branch(user, branch):
        raise PermissionDenied("You do not have access to this branch.")
    return [branch_id]
