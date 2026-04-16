from django.urls import path

from . import views

urlpatterns = [
    path("banking/link-token/", views.LinkTokenView.as_view(), name="banking-link-token"),
    path(
        "banking/exchange-token/",
        views.ExchangePublicTokenView.as_view(),
        name="banking-exchange-token",
    ),
    path(
        "banking/connections/",
        views.BankConnectionsView.as_view(),
        name="banking-connections",
    ),
    path(
        "banking/accounts/",
        views.BankAccountsView.as_view(),
        name="banking-accounts",
    ),
    path(
        "banking/transactions/",
        views.BankTransactionsView.as_view(),
        name="banking-transactions",
    ),
    # PATCH /api/banking/transactions/<plaid_transaction_id>/
    # Accepts user feedback and stores it on the associated feedback Transaction.
    # Uses plaid_transaction_id (string) as the URL key — matching what the
    # frontend BankingAPI.submitFeedback sends.
    path(
        "banking/transactions/<str:plaid_transaction_id>/",
        views.BankTransactionFeedbackView.as_view(),
        name="banking-transaction-feedback",
    ),
    path(
        "banking/transactions/<int:transaction_id>/score/",
        views.BankTransactionScoreView.as_view(),
        name="banking-transaction-score",
    ),
    path(
        "banking/connections/<int:connection_id>/sync/",
        views.ManualSyncView.as_view(),
        name="banking-manual-sync",
    ),
]
