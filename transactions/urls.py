from django.urls import include, path
from rest_framework.routers import DefaultRouter
from .views import TransactionViewSet

router = DefaultRouter()
router.include_format_suffixes = False
router.register(r"transactions", TransactionViewSet, basename="transactions")

urlpatterns = [
    path("", include(router.urls)),
]
 