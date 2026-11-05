from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import BusinessRegistrationView, BusinessViewSet

router = DefaultRouter()
router.register("me", BusinessViewSet, basename="business")

urlpatterns = [
    path("register/", BusinessRegistrationView.as_view(), name="business-register"),
    path("", include(router.urls)),
]