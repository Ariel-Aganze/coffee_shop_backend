import logging

from django.conf import settings
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import send_mail
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.exceptions import PermissionDenied
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from apps.audit.services import log_action

from .models import User
from .serializers import (
    BRANCH_MANAGED_ROLES,
    ChangePasswordSerializer,
    LoginRequestSerializer,
    LoginResponseSerializer,
    LogoutSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
    UserCreateSerializer,
    UserSerializer,
    UserUpdateSerializer,
    build_uid_token,
)

from rest_framework import viewsets
from drf_spectacular.utils import extend_schema

from config.schema import DetailSerializer, ErrorSerializer

from .constants import UserRole
from .permissions import HasAnyRole, IsAdminOrOwner, IsOwner

logger = logging.getLogger(__name__)


class LoginView(APIView):
    """
    POST /api/auth/login/
    Body: { "username": "...", "password": "..." }
    Returns: { access, refresh, user }
    """
    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(request=LoginRequestSerializer, responses={200: LoginResponseSerializer, 400: ErrorSerializer, 401: ErrorSerializer, 403: ErrorSerializer}, auth=[])
    def post(self, request):
        username = request.data.get("username", "").strip()
        password = request.data.get("password", "")

        if not username or not password:
            return Response(
                {"error": "Username and password are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user = User.objects.filter(username=username).first()
        if not user or not user.check_password(password):
            return Response(
                {"error": "Invalid credentials."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        if not user.is_active:
            return Response(
                {"error": "This account is deactivated (AUTH-009)."},
                status=status.HTTP_403_FORBIDDEN,
            )

        refresh = RefreshToken.for_user(user)
        log_action(user=user, business=user.business, action="user_logged_in")

        return Response(
            {
                "access": str(refresh.access_token),
                "refresh": str(refresh),
                "user": UserSerializer(user).data,
            },
            status=status.HTTP_200_OK,
        )


class LogoutView(APIView):
    """
    POST /api/auth/logout/
    Body: { "refresh": "..." }
    Blacklists the refresh token.
    """
    permission_classes = [IsAuthenticated]

    @extend_schema(request=LogoutSerializer, responses={204: None, 400: ErrorSerializer})
    def post(self, request):
        serializer = LogoutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            token = RefreshToken(serializer.validated_data["refresh"])
            token.blacklist()
        except TokenError:
            return Response(
                {"error": "Token is invalid or already blacklisted."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        log_action(user=request.user, business=request.user.business,
                   action="user_logged_out")
        return Response(status=status.HTTP_204_NO_CONTENT)


class ChangePasswordView(APIView):
    """
    POST /api/auth/change-password/
    """
    permission_classes = [IsAuthenticated]

    @extend_schema(request=ChangePasswordSerializer, responses=DetailSerializer)
    def post(self, request):
        serializer = ChangePasswordSerializer(
            data=request.data, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        request.user.set_password(serializer.validated_data["new_password"])
        request.user.save(update_fields=["password"])
        log_action(user=request.user, business=request.user.business,
                   action="password_changed")
        return Response({"detail": "Password updated successfully."})


class PasswordResetRequestView(APIView):
    """
    POST /api/auth/password-reset/
    Body: { "email": "..." }
    Always returns 200 (no user enumeration).
    """
    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(request=PasswordResetRequestSerializer, responses=DetailSerializer, auth=[])
    def post(self, request):
        serializer = PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = User.objects.filter(
            email__iexact=serializer.validated_data["email"], is_active=True,
        ).first()
        if user:
            # The uid/token must ONLY travel by email — returning them in the
            # response would let anyone reset any account by its email address.
            uid, token = build_uid_token(user)
            reset_url = (
                f"{settings.FRONTEND_URL.rstrip('/')}/reset-password"
                f"?uid={uid}&token={token}"
            )
            try:
                send_mail(
                    subject="Reset your password",
                    message=(
                        "A password reset was requested for your account.\n\n"
                        f"Open this link to choose a new password:\n{reset_url}\n\n"
                        "If you did not request this, you can ignore this email."
                    ),
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[user.email],
                )
            except Exception:
                logger.exception("Failed to send password reset email (user id=%s)", user.pk)
            log_action(user=user, business=user.business,
                       action="password_reset_requested")
        # Identical response whether or not the email exists (no enumeration).
        return Response(
            {"detail": "If the email exists, a reset link was sent."}
        )


class PasswordResetConfirmView(APIView):
    """
    POST /api/auth/password-reset/confirm/
    Body: { "uid": "...", "token": "...", "new_password": "..." }
    """
    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(request=PasswordResetConfirmSerializer, responses=DetailSerializer, auth=[])
    def post(self, request):
        serializer = PasswordResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data["user"]
        user.set_password(serializer.validated_data["new_password"])
        user.save(update_fields=["password"])
        log_action(user=user, business=user.business, action="password_reset")
        return Response({"detail": "Password has been reset."})


@extend_schema(responses=UserSerializer)
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def me_view(request):
    """
    GET /api/auth/me/
    Returns the authenticated user's profile.
    """
    return Response(UserSerializer(request.user).data)





class UserViewSet(viewsets.ModelViewSet):
    queryset = User.objects.none()  # schema only; real queryset in get_queryset
    """
    /api/users/
    - Owner: manages every employee of the business.
    - Admin: manages employees, but not the owner or other admins.
    - Branch manager: manages cashiers and inventory managers assigned to
      their own branches (RBAC 4.2 "Manage employees: own branch").
    Accounts are deactivated, never deleted (AUTH-008). Every account and
    permission change is audited (AUTH-011, AUD-008, 4.3).
    """
    permission_classes = [HasAnyRole]
    allowed_roles = (UserRole.OWNER, UserRole.ADMIN, UserRole.BRANCH_MANAGER)

    def get_serializer_class(self):
        if self.action == "create":
            return UserCreateSerializer
        if self.action in ("update", "partial_update"):
            return UserUpdateSerializer
        return UserSerializer

    def get_queryset(self):
        qs = User.objects.filter(
            business_id=self.request.user.business_id
        ).exclude(id=self.request.user.id).order_by("-date_joined")

        # Branch managers only see users who are assigned to their branches
        if self.request.user.role == UserRole.BRANCH_MANAGER:
            their_branches = self.request.user.assigned_branch_ids
            qs = qs.filter(branch_assignments__branch_id__in=their_branches).distinct()

        return qs

    def _check_can_manage(self, target):
        """Writes are only allowed on users below the actor."""
        actor = self.request.user
        if actor.role == UserRole.OWNER:
            return
        if target.role in (UserRole.OWNER, UserRole.ADMIN):
            raise PermissionDenied("Only the owner can change owner or admin accounts.")
        if actor.role == UserRole.BRANCH_MANAGER and target.role not in BRANCH_MANAGED_ROLES:
            raise PermissionDenied(
                "Branch managers can only manage cashiers and inventory managers."
            )

    @staticmethod
    def _snapshot(user) -> dict:
        return {
            "role": user.role,
            "is_active": user.is_active,
            "branch_ids": sorted(user.assigned_branch_ids),
        }

    def _audit(self, action, target, previous=None):
        log_action(
            business=self.request.user.business,
            user=self.request.user,
            action=action,
            target_type="User",
            target_id=str(target.pk),
            metadata={"username": target.username},
            previous_value=previous,
            new_value=self._snapshot(target),
        )

    def perform_create(self, serializer):
        user = serializer.save()
        self._audit("user_created", user)

    def perform_update(self, serializer):
        instance = serializer.instance
        self._check_can_manage(instance)
        before = self._snapshot(instance)
        user = serializer.save()
        after = self._snapshot(user)
        if before != after:
            action = (
                "user_deactivated" if before["is_active"] and not after["is_active"]
                else "user_permissions_changed"
            )
            self._audit(action, user, previous=before)
        else:
            self._audit("user_updated", user, previous=before)

    def perform_destroy(self, instance):
        # AUTH-008: deactivate instead of hard delete for staff
        self._check_can_manage(instance)
        before = self._snapshot(instance)
        instance.is_active = False
        instance.save(update_fields=["is_active"])
        self._audit("user_deactivated", instance, previous=before)
