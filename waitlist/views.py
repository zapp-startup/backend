from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import WaitlistSignup
from .serializers import WaitlistSignupSerializer
from .throttles import WaitlistSignupThrottle


class WaitlistSignupView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [WaitlistSignupThrottle]

    def post(self, request):
        serializer = WaitlistSignupSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        signup, created = WaitlistSignup.objects.update_or_create(
            email=serializer.validated_data["email"],
            defaults={
                "name": serializer.validated_data["name"],
                "source": serializer.validated_data.get("source", "landing_page"),
            },
        )

        response_serializer = WaitlistSignupSerializer(signup)
        payload = {
            **response_serializer.data,
            "created": created,
        }
        return Response(
            payload,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )
