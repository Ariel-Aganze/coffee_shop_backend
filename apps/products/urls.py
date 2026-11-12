from django.urls import path
from rest_framework.routers import DefaultRouter

from .catalog import PosCatalogView
from .views import (
    BranchProductAvailabilityViewSet,
    CategoryViewSet,
    ProductViewSet,
)

router = DefaultRouter()
router.register("categories", CategoryViewSet, basename="category")
router.register("products", ProductViewSet, basename="product")
router.register("branch-availability", BranchProductAvailabilityViewSet,
                basename="branch-availability")

urlpatterns = [
    path("pos/catalog/", PosCatalogView.as_view(), name="pos-catalog"),
] + router.urls
