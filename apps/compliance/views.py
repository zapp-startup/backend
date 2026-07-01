import logging

from django.conf import settings
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.users.session_authentication import AuthSessionAuthentication

from .models import ConsentType
from .serializers import AuditEventIngestSerializer
from .throttles import AuditEventIngestThrottle, ComplianceConsentThrottle
from .services import current_policy_version, record_financial_consent, user_has_valid_financial_consent

logger = logging.getLogger(__name__)


class PrivacyPolicyMetadataView(APIView):
    """
    Public metadata for the hosted privacy policy (URLs and version for client sync).
    """

    permission_classes = [AllowAny]

    def get(self, request):
        return Response(
            {
                "privacy_policy_url": getattr(settings, "PRIVACY_POLICY_URL", ""),
                "privacy_policy_version": current_policy_version(),
                "privacy_policy_effective_date": getattr(
                    settings, "PRIVACY_POLICY_EFFECTIVE_DATE", ""
                ),
            }
        )


class FinancialConsentStatusView(APIView):
    """Whether the current user has accepted the active policy for financial data access."""

    authentication_classes = [AuthSessionAuthentication]
    permission_classes = [IsAuthenticated]
    throttle_classes = [ComplianceConsentThrottle]

    def get(self, request):
        ok = user_has_valid_financial_consent(request.user)
        return Response(
            {
                "has_valid_financial_consent": ok,
                "required_policy_version": current_policy_version(),
            }
        )


class RecordFinancialConsentView(APIView):
    """
    Record acceptance of financial data access for the current policy version.
    Optional body: { "consent_text": "...", "source": "web" }
    """

    authentication_classes = [AuthSessionAuthentication]
    permission_classes = [IsAuthenticated]
    throttle_classes = [ComplianceConsentThrottle]

    def post(self, request):
        text = (request.data.get("consent_text") or "").strip() or None
        source = (request.data.get("source") or "api").strip()[:32]

        obj, created = record_financial_consent(
            user=request.user,
            consent_type=ConsentType.FINANCIAL_DATA_ACCESS,
            consent_text=text,
            source=source,
            request=request,
        )
        if not created and obj is None:
            return Response(
                status=status.HTTP_204_NO_CONTENT,
            )
        return Response(
            {
                "recorded": True,
                "policy_version": current_policy_version(),
                "consent_type": ConsentType.FINANCIAL_DATA_ACCESS,
            },
            status=status.HTTP_201_CREATED,
        )


class AuditEventIngestView(APIView):
    """
    Persist frontend-emitted audit events.
    Trust-sensitive identity fields are derived from the authenticated request,
    not the client payload.
    """

    authentication_classes = [AuthSessionAuthentication]
    permission_classes = [IsAuthenticated]
    throttle_classes = [AuditEventIngestThrottle]

    def post(self, request):
        serializer = AuditEventIngestSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(status=status.HTTP_201_CREATED)
