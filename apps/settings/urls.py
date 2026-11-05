from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import BusinessSettingsViewSet, CurrencyListView

router = DefaultRouter()
router.register("me", BusinessSettingsViewSet, basename="settings")

urlpatterns = [
    path("currencies/", CurrencyListView.as_view(), name="currency-list"),
    path("", include(router.urls)),
]