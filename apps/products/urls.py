from rest_framework.routers import DefaultRouter

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

urlpatterns = router.urls