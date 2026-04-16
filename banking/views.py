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
    BankTransactionFeedbackSerializer,
    BankTransactionSerializer,
    ExchangePublicTokenSerializer,
)
from .services import (
    create_link_token_for_user,
    exchange_public_token_for_user,
    sync_transactions_for_connection,
)
from .throttles import BankingLinkTokenThrottle, BankingSensitiveThrottle
from transactions.services.feedback_scoring import apply_feedback_scoring
from valuations.services.transaction_value_score import ensure_bank_feedback_transaction, persist_transaction_value_score
from valuations.services.value_score_inference import ValueScoreModelNotAvailable
from users.session_authentication import AuthSessionAuthentication

logger = logging.getLogger(__name__)


class LinkTokenView(APIView):
    """
    Create a Plaid link_token for the authenticated user.
    Frontend uses this to initialize Plaid Link.
    """

    authentication_classes = [AuthSessionAuthentication]
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
        except ValueError:
            logger.exception(
                "banking_link_token_failed user_id=%s",
                request.user.pk,
            )
            return Response(
                {"error": "Failed to create link token"},
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

    authentication_classes = [AuthSessionAuthentication]
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
        except Exception:
            logger.exception("Plaid token exchange failed")
            return Response(
                {"error": "Failed to exchange token"},
                status=status.HTTP_400_BAD_REQUEST,
            )


class BankConnectionsView(APIView):
    """
    List user's bank connections.
    """

    authentication_classes = [AuthSessionAuthentication]
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

    authentication_classes = [AuthSessionAuthentication]
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

    authentication_classes = [AuthSessionAuthentication]
    permission_classes = [IsAuthenticated]
    throttle_classes = [BankingSensitiveThrottle]

    def get(self, request):
        enforce_banking_policies(request)
        qs = (
            BankTransaction.objects.filter(user=request.user)
            .select_related("account", "connection", "feedback_transaction")
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


class BankTransactionScoreView(APIView):
    """
    Persist a model-backed value score for one bank transaction.
    """

    authentication_classes = [AuthSessionAuthentication]
    permission_classes = [IsAuthenticated]
    throttle_classes = [BankingSensitiveThrottle]

    def post(self, request, transaction_id):
        enforce_banking_policies(request)
        try:
            bank_transaction = (
                BankTransaction.objects
                .select_related("feedback_transaction")
                .get(id=transaction_id, user=request.user, removed=False)
            )
        except BankTransaction.DoesNotExist:
            return Response({"error": "Transaction not found"}, status=status.HTTP_404_NOT_FOUND)

        feedback_transaction = ensure_bank_feedback_transaction(bank_transaction)
        try:
            persist_transaction_value_score(feedback_transaction)
        except ValueScoreModelNotAvailable as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        bank_transaction.refresh_from_db()
        return Response(BankTransactionSerializer(bank_transaction).data, status=status.HTTP_200_OK)


class BankTransactionFeedbackView(APIView):
    """
    PATCH /api/banking/transactions/<plaid_transaction_id>/

    Accept user feedback (satisfaction, regret, repurchase, usage, reflection)
    for a bank-synced transaction.  Feedback is stored on the associated
    feedback Transaction record (not on BankTransaction, which mirrors Plaid).

    This endpoint mirrors what PATCH /api/transactions/<id>/ does for manually
    tracked transactions, keeping both flows behaviourally consistent.
    """

    authentication_classes = [AuthSessionAuthentication]
    permission_classes = [IsAuthenticated]
    throttle_classes = [BankingSensitiveThrottle]

    def patch(self, request, plaid_transaction_id: str):
        enforce_banking_policies(request)
        try:
            bank_transaction = (
                BankTransaction.objects
                .select_related("feedback_transaction")
                .get(
                    plaid_transaction_id=plaid_transaction_id,
                    user=request.user,
                    removed=False,
                )
            )
        except BankTransaction.DoesNotExist:
            return Response({"error": "Transaction not found"}, status=status.HTTP_404_NOT_FOUND)

        serializer = BankTransactionFeedbackSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data

        # Ensure a feedback Transaction exists (creates one if missing).
        feedback_tx = ensure_bank_feedback_transaction(bank_transaction)

        # Apply the writable feedback fields to the feedback transaction.
        FEEDBACK_FIELDS = [
            "satisfaction_rating",
            "regret_rating",
            "repurchase_likelihood",
            "usage_frequency",
            "reflection_text",
            "considered_at",
        ]
        update_fields = []
        for field in FEEDBACK_FIELDS:
            if field in data:
                value = data[field]
                # considered_at sanity check: discard if >= occurred_at
                # (same rule as TransactionSerializer._sanitize_considered_at).
                if field == "considered_at" and value is not None:
                    if value >= feedback_tx.occurred_at:
                        logger.debug(
                            "bank feedback: considered_at (%s) >= occurred_at (%s); discarding",
                            value,
                            feedback_tx.occurred_at,
                        )
                        value = None
                setattr(feedback_tx, field, value)
                update_fields.append(field)

        if update_fields:
            feedback_tx.save(update_fields=update_fields)

        # Derive feedback_value_score / feedback_confidence from ratings.
        apply_feedback_scoring(feedback_tx, save=True)

        from transactions.services.events import mark_transaction_dirty
        mark_transaction_dirty(feedback_tx, reason="bank_transaction_feedback", priority=4)

        bank_transaction.refresh_from_db()
        return Response(BankTransactionSerializer(bank_transaction).data, status=status.HTTP_200_OK)


class ManualSyncView(APIView):
    """
    Manually trigger transactions/sync for a bank connection.
    """

    authentication_classes = [AuthSessionAuthentication]
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
        except Exception:
            logger.exception(
                "banking_manual_sync_failed user_id=%s connection_id=%s",
                request.user.pk,
                connection_id,
            )
            return Response(
                {"error": "Sync failed"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
