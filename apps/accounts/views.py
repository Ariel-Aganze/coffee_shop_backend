from django.conf import settings
from django.contrib.auth.tokens import default_token_generator
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from apps.audit.services import log_action

from .models import User
from .serializers import (
    ChangePasswordSerializer,
    LogoutSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
    UserCreateSerializer,
    UserSerializer,
    UserUpdateSerializer,
    build_uid_token,
)

from rest_framework import viewsets

from .permissions import IsAdminOrOwner, IsOwner


class LoginView(APIView):
    """
    POST /api/auth/login/
    Body: { "username": "...", "password": "..." }
    Returns: { access, refresh, user }
    """
    permission_classes = [AllowAny]
    authentication_classes = []

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

    def post(self, request):
        serializer = PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = User.objects.filter(email=serializer.validated_data["email"]).first()
        if user:
            uid, token = build_uid_token(user)
            # TODO: send email with uid+token.
            # In dev, we return them so you can test the flow.
            return Response(
                {
                    "detail": "If the email exists, a reset link was sent.",
                    "uid": uid,
                    "token": token,
                }
            )
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

    def post(self, request):
        serializer = PasswordResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data["user"]
        user.set_password(serializer.validated_data["new_password"])
        user.save(update_fields=["password"])
        log_action(user=user, business=user.business, action="password_reset")
        return Response({"detail": "Password has been reset."})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def me_view(request):
    """
    GET /api/auth/me/
    Returns the authenticated user's profile.
    """
    return Response(UserSerializer(request.user).data)





class UserViewSet(viewsets.ModelViewSet):
    """
    /api/users/
    - List/retrieve: owner sees all; branch managers see their business staff.
    - Create/update/delete: owner (and branch managers for their staff roles).
    """
    permission_classes = [IsAdminOrOwner]

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
        if self.request.user.role == "branch_manager":
            their_branches = self.request.user.assigned_branch_ids
            qs = qs.filter(branch_assignments__branch_id__in=their_branches).distinct()

        return qs

    def perform_create(self, serializer):
        serializer.save()

    def perform_update(self, serializer):
        # Prevent non-owners from elevating roles to owner/admin
        instance = serializer.instance
        if self.request.user.role != "owner":
            forbidden = {"owner", "admin"}
            new_role = serializer.validated_data.get("role")
            if new_role in forbidden and instance.role != new_role:
                from rest_framework.exceptions import PermissionDenied
                raise PermissionDenied("Only the owner can assign this role.")
        serializer.save()

    def perform_destroy(self, instance):
        # AUTH-008: deactivate instead of hard delete for staff
        instance.is_active = False
        instance.save(update_fields=["is_active"])