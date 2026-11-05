from rest_framework.routers import DefaultRouter

from .views import InventoryViewSet, StockMovementViewSet

router = DefaultRouter()
router.register("inventory", InventoryViewSet, basename="inventory")
router.register("stock-movements", StockMovementViewSet, basename="stock-movement")

urlpatterns = router.urls