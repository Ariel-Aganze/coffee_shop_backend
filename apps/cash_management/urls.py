from rest_framework.routers import DefaultRouter

from .views import CashSessionViewSet

router = DefaultRouter()
router.register("cash-sessions", CashSessionViewSet, basename="cash-session")

urlpatterns = router.urls