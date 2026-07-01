from rest_framework.pagination import CursorPagination


class TransactionCursorPagination(CursorPagination):
    """Keyset pagination for the user's transaction timeline.

    Cursor pagination is COUNT-free and stable under concurrent inserts, which
    suits an append-heavy, newest-first ledger: paging back through older
    transactions never shifts when new ones land at the top.

    Ordered by ``-occurred_at`` to match ``Transaction.Meta.ordering`` and ride
    the existing ``(user, occurred_at)`` composite index. ``occurred_at`` is a
    plain ``DateTimeField`` (not encrypted), so the database orders it by real
    timestamp value rather than ciphertext.
    """

    page_size = 25
    max_page_size = 100
    page_size_query_param = "page_size"
    cursor_query_param = "cursor"
    ordering = "-occurred_at"
