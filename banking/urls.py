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
    path(
        "banking/connections/<int:connection_id>/sync/",
        views.ManualSyncView.as_view(),
        name="banking-manual-sync",
    ),
]
