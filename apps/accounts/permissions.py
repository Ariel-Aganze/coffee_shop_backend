from rest_framework.permissions import BasePermission

from .constants import UserRole


class IsOwner(BasePermission):
    message = "Only the business owner can perform this action."

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.role == UserRole.OWNER
        )


class IsAdminOrOwner(BasePermission):
    message = "Only the owner or an administrator may perform this action."

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.role in (UserRole.OWNER, UserRole.ADMIN)
        )


class IsOwnerOrBranchManager(BasePermission):
    message = "Only the owner or a branch manager may perform this action."

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.role in (UserRole.OWNER, UserRole.BRANCH_MANAGER)
        )


class IsOwnerOrAccountant(BasePermission):
    message = "Only the owner or an accountant may perform this action."

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.role in (UserRole.OWNER, UserRole.ACCOUNTANT)
        )


class HasAnyRole(BasePermission):
    """
    Generic role gate. Set `allowed_roles` on the view.
    """
    allowed_roles: tuple[str, ...] = ()

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        allowed = getattr(view, "allowed_roles", self.allowed_roles)
        return not allowed or request.user.role in allowed


class CanAccessBranch(BasePermission):
    """
    Object-level check: user may access a branch only if
    - they are the owner, or
    - the branch belongs to their business and is assigned to them.
    Works on any object that exposes a `.branch` attribute.
    """
    message = "You do not have access to this branch."

    def has_object_permission(self, request, view, obj):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if user.role == UserRole.OWNER:
            return True
        branch = getattr(obj, "branch", None) or obj
        if getattr(branch, "business_id", None) != user.business_id:
            return False
        return branch.id in user.assigned_branch_ids