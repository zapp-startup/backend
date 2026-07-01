from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .session_authentication import AuthSessionAuthentication
from .security_assurance import build_assurance_payload


class AuthAssuranceView(APIView):
    """
    Exposes current session assurance / MFA-related state for the frontend.
    """

    authentication_classes = [AuthSessionAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(build_assurance_payload(request))
