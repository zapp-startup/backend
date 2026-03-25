from django.urls import include, path
from rest_framework.routers import DefaultRouter
from .views import TransactionReflectionViewSet, TransactionViewSet

router = DefaultRouter()
router.include_format_suffixes = False
router.register(r"transactions", TransactionViewSet, basename="transactions")
router.register(r"transaction-reflections", TransactionReflectionViewSet, basename="transaction-reflections")

urlpatterns = [
    path("", include(router.urls)),
]
 
