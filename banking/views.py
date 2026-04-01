import logging

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import BankAccount, BankConnection, BankTransaction
from .security_checks import enforce_banking_policies
from .serializers import (
    BankAccountListSerializer,
    BankConnectionSerializer,
    BankTransactionSerializer,
    ExchangePublicTokenSerializer,
)
from .services import (
    create_link_token_for_user,
    exchange_public_token_for_user,
    sync_transactions_for_connection,
)
from .throttles import BankingLinkTokenThrottle, BankingSensitiveThrottle

logger = logging.getLogger(__name__)


class LinkTokenView(APIView):
    """
    Create a Plaid link_token for the authenticated user.
    Frontend uses this to initialize Plaid Link.
    """

    permission_classes = [IsAuthenticated]
    throttle_classes = [BankingLinkTokenThrottle]

    def post(self, request):
        enforce_banking_policies(request)
        try:
            link_token = create_link_token_for_user(request.user)
            logger.info(
                "banking_link_token_created user_id=%s",
                request.user.pk,
            )
            return Response({"link_token": link_token})
        except ValueError as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        except Exception:
            return Response(
                {"error": "Failed to create link token"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class ExchangePublicTokenView(APIView):
    """
    Exchange public_token from Plaid Link for access_token.
    Creates connection, fetches accounts, runs initial transaction sync.
    Requires authentication.
    """

    permission_classes = [IsAuthenticated]
    throttle_classes = [BankingSensitiveThrottle]

    def post(self, request):
        enforce_banking_policies(request)
        serializer = ExchangePublicTokenSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        public_token = serializer.validated_data["public_token"]
        try:
            connection = exchange_public_token_for_user(request.user, public_token)
            logger.info(
                "banking_connection_created user_id=%s connection_id=%s plaid_item_id=%s",
                request.user.pk,
                connection.pk,
                connection.plaid_item_id,
            )
            return Response(
                {
                    "success": True,
                    "connection": BankConnectionSerializer(connection).data,
                },
                status=status.HTTP_201_CREATED,
            )
        except Exception as e:
            logger.exception("Plaid token exchange failed")
            return Response(
                {"error": "Failed to exchange token", "detail": str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )


class BankConnectionsView(APIView):
    """
    List user's bank connections.
    """

    permission_classes = [IsAuthenticated]
    throttle_classes = [BankingSensitiveThrottle]

    def get(self, request):
        enforce_banking_policies(request)
        connections = BankConnection.objects.filter(user=request.user).order_by(
            "-created_at"
        )
        serializer = BankConnectionSerializer(connections, many=True)
        return Response(serializer.data)


class BankAccountsView(APIView):
    """
    List user's linked bank accounts from our DB.
    """

    permission_classes = [IsAuthenticated]
    throttle_classes = [BankingSensitiveThrottle]

    def get(self, request):
        enforce_banking_policies(request)
        accounts = BankAccount.objects.filter(
            connection__user=request.user
        ).select_related("connection")
        serializer = BankAccountListSerializer(accounts, many=True)
        return Response(serializer.data)


class BankTransactionsView(APIView):
    """
    List user's bank transactions from our DB.
    Supports filtering by account_id, date_from, date_to, pending, removed.
    """

    permission_classes = [IsAuthenticated]
    throttle_classes = [BankingSensitiveThrottle]

    def get(self, request):
        enforce_banking_policies(request)
        qs = (
            BankTransaction.objects.filter(user=request.user)
            .select_related("account", "connection")
            .order_by("-date", "-created_at")
        )

        account_id = request.query_params.get("account_id")
        if account_id:
            qs = qs.filter(account_id=account_id)

        date_from = request.query_params.get("date_from")
        if date_from:
            qs = qs.filter(date__gte=date_from)

        date_to = request.query_params.get("date_to")
        if date_to:
            qs = qs.filter(date__lte=date_to)

        pending = request.query_params.get("pending")
        if pending is not None:
            qs = qs.filter(pending=(pending.lower() == "true"))

        removed = request.query_params.get("removed")
        if removed is not None:
            qs = qs.filter(removed=(removed.lower() == "true"))
        else:
            qs = qs.filter(removed=False)

        limit = request.query_params.get("limit")
        if limit:
            try:
                qs = qs[: int(limit)]
            except ValueError:
                pass

        serializer = BankTransactionSerializer(qs, many=True)
        return Response(serializer.data)


class ManualSyncView(APIView):
    """
    Manually trigger transactions/sync for a bank connection.
    """

    permission_classes = [IsAuthenticated]
    throttle_classes = [BankingSensitiveThrottle]

    def post(self, request, connection_id):
        enforce_banking_policies(request)
        try:
            connection = BankConnection.objects.get(
                id=connection_id,
                user=request.user,
            )
        except BankConnection.DoesNotExist:
            return Response(
                {"error": "Connection not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            logger.info(
                "banking_manual_sync user_id=%s connection_id=%s",
                request.user.pk,
                connection_id,
            )
            result = sync_transactions_for_connection(connection, cursor=None)
            return Response(
                {
                    "success": True,
                    "connection": BankConnectionSerializer(connection).data,
                    "sync_result": result,
                }
            )
        except Exception as e:
            return Response(
                {"error": "Sync failed", "detail": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
