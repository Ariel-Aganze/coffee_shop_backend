from django.urls import path

from .views import BusinessRegistrationView, BusinessViewSet

# The user's business is a singleton ("me"), routed without an /<id>/.
business_detail = BusinessViewSet.as_view({"get": "retrieve", "patch": "partial_update"})

urlpatterns = [
    path("register/", BusinessRegistrationView.as_view(), name="business-register"),
    path("me/", business_detail, name="business-detail"),
]
