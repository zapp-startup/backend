from django.urls import path

from . import views

urlpatterns = [
    path(
        "compliance/privacy-policy/",
        views.PrivacyPolicyMetadataView.as_view(),
        name="compliance-privacy-policy",
    ),
    path(
        "compliance/consent/status/",
        views.FinancialConsentStatusView.as_view(),
        name="compliance-consent-status",
    ),
    path(
        "compliance/consent/",
        views.RecordFinancialConsentView.as_view(),
        name="compliance-consent-record",
    ),
]
