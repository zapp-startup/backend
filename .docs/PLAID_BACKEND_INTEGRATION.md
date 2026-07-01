# Plaid Backend Integration

## 1. Files Added/Modified

### Added
- `banking/` – New Django app
  - `banking/__init__.py`
  - `banking/apps.py`
  - `banking/admin.py`
  - `banking/models.py` – BankConnection, BankAccount, BankTransaction
  - `banking/serializers.py`
  - `banking/views.py`
  - `banking/urls.py`
  - `banking/tests.py`
  - `banking/services/__init__.py`
  - `banking/services/plaid_service.py`
  - `banking/migrations/0001_initial.py`

### Modified
- `zapp/settings/base.py` – Added `banking` to `INSTALLED_APPS`
- `zapp/urls.py` – Added `path("api/", include("banking.urls"))`
- `.docs/requirements.txt` – Added `plaid-python>=20.0.0`
- `.env.example` – Added Plaid env vars

---

## 2. Models/Tables Added

| Model | Table | Purpose |
|-------|------|---------|
| `BankConnection` | `banking_bankconnection` | One Plaid Item per user; stores `plaid_item_id`, `plaid_access_token`, `sync_cursor`, institution info |
| `BankAccount` | `banking_bankaccount` | Accounts under a connection; `plaid_account_id`, balances, type, subtype |
| `BankTransaction` | `banking_banktransaction` | Synced Plaid transactions; `plaid_transaction_id`, amount, date, categories, `removed` flag |

---

## 3. Endpoints Added

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/banking/link-token/` | Create Plaid `link_token` for frontend Link |
| POST | `/api/banking/exchange-token/` | Exchange `public_token` → store connection, fetch accounts, initial sync |
| GET | `/api/banking/connections/` | List user's bank connections |
| GET | `/api/banking/accounts/` | List user's linked accounts |
| GET | `/api/banking/transactions/` | List stored transactions (with filters) |
| POST | `/api/banking/connections/<id>/sync/` | Manual transaction sync for a connection |

---

## 4. Service/Helper Functions

| Function | Purpose |
|---------|---------|
| `create_link_token_for_user(user)` | Create Plaid `link_token` for Link initialization |
| `exchange_public_token_for_user(user, public_token)` | Exchange token, create connection, fetch accounts, initial sync |
| `fetch_accounts_for_connection(connection)` | Call `/accounts/get`, upsert accounts |
| `sync_transactions_for_connection(connection, cursor=None)` | Call `/transactions/sync`, upsert transactions, update cursor |
| `upsert_accounts_from_plaid(connection, accounts_payload)` | Upsert `BankAccount` from Plaid accounts |
| `upsert_transactions_from_plaid(connection, sync_payload)` | Upsert/remove `BankTransaction` from sync response |

---

## 5. Request/Response Contracts (Frontend Handoff)

### A. Create Link Token
**Request:** `POST /api/banking/link-token/`  
**Headers:** `Authorization: Bearer <supabase_access_token>`  
**Body:** (none)

**Response 200:**
```json
{
  "link_token": "link-sandbox-abc123..."
}
```

---

### B. Exchange Public Token
**Request:** `POST /api/banking/exchange-token/`  
**Headers:** `Authorization: Bearer <supabase_access_token>`  
**Body:**
```json
{
  "public_token": "public-sandbox-xyz..."
}
```

**Response 201:**
```json
{
  "success": true,
  "connection": {
    "id": 1,
    "plaid_item_id": "eVBnVMp7zdTJLkRNr33Rs6zr7KNJqBFL9DrE6",
    "institution_id": "ins_3",
    "institution_name": "Chase",
    "status": "active",
    "last_synced_at": "2025-03-10T04:00:00Z",
    "created_at": "2025-03-10T04:00:00Z",
    "updated_at": "2025-03-10T04:00:00Z"
  }
}
```

---

### C. Read Linked Accounts
**Request:** `GET /api/banking/accounts/`  
**Headers:** `Authorization: Bearer <supabase_access_token>`

**Response 200:**
```json
[
  {
    "id": 1,
    "plaid_account_id": "vzeNDwK7KQIm4yEog683uElbp9GRLEFXGK98D",
    "name": "Plaid Checking",
    "official_name": "Plaid Gold Standard 0% Interest Checking",
    "mask": "0000",
    "type": "depository",
    "subtype": "checking",
    "current_balance": "110.00",
    "available_balance": "100.00",
    "iso_currency_code": "USD"
  }
]
```

---

### D. Read Transactions
**Request:** `GET /api/banking/transactions/`  
**Headers:** `Authorization: Bearer <supabase_access_token>`  
**Query params (optional):**
- `account_id` – Filter by account ID
- `date_from` – YYYY-MM-DD
- `date_to` – YYYY-MM-DD
- `pending` – `true` / `false`
- `removed` – `true` / `false` (default: `false`)
- `limit` – Max number of results

**Response 200:**
```json
[
  {
    "id": 1,
    "account": {
      "id": 1,
      "plaid_account_id": "vzeNDwK7KQIm4yEog683uElbp9GRLEFXGK98D",
      "name": "Plaid Checking",
      "official_name": "Plaid Gold Standard 0% Interest Checking",
      "mask": "0000",
      "type": "depository",
      "subtype": "checking",
      "current_balance": "110.00",
      "available_balance": "100.00",
      "iso_currency_code": "USD"
    },
    "plaid_transaction_id": "txn_123",
    "name": "Uber",
    "merchant_name": "Uber",
    "amount": "-15.50",
    "iso_currency_code": "USD",
    "date": "2025-03-09",
    "authorized_date": "2025-03-09",
    "pending": false,
    "removed": false,
    "category_primary": "Travel",
    "category_detailed": "Travel, Taxis",
    "created_at": "2025-03-10T04:00:00Z",
    "updated_at": "2025-03-10T04:00:00Z"
  }
]
```

---

### E. Manual Sync
**Request:** `POST /api/banking/connections/<connection_id>/sync/`  
**Headers:** `Authorization: Bearer <supabase_access_token>`  
**Body:** (none)

**Response 200:**
```json
{
  "success": true,
  "connection": { ... },
  "sync_result": {
    "added_count": 5,
    "modified_count": 0,
    "removed_count": 0,
    "next_cursor": "..."
  }
}
```

---

## 6. Env Vars Required

| Variable | Required | Description |
|----------|----------|-------------|
| `PLAID_CLIENT_ID` | Yes | Plaid client ID |
| `PLAID_SECRET` | Yes | Plaid secret (use Sandbox secret for sandbox) |
| `PLAID_ENV` | No | `sandbox` \| `development` \| `production` (default: sandbox) |
| `PLAID_PRODUCTS` | No | Comma-separated, e.g. `transactions` (default: transactions) |
| `PLAID_COUNTRY_CODES` | No | Comma-separated, e.g. `US` (default: US) |
| `PLAID_WEBHOOK_URL` | No | For future webhook support |
| `PLAID_REDIRECT_URI` | No | OAuth redirect if needed |
| `PLAID_CLIENT_NAME` | No | App name in Link (default: Zapp) |

---

## 7. Migration and Run Steps

### Install dependencies
```bash
pip install -r .docs/requirements.txt
```

### Run migrations
```bash
python manage.py migrate banking
```

### Run server
```bash
python manage.py runserver
```

### Run tests
```bash
python manage.py test banking
```

---

## 8. Deviations from Codex Plan

| Change | Reason |
|--------|--------|
| **Model name `BankTransaction`** | Repo already has `transactions.Transaction` for user feedback ledger. `BankTransaction` avoids collision. |
| **`PLAID_ENV` "development" → Sandbox** | Plaid Python SDK only exposes `Sandbox` and `Production`. Development env maps to Sandbox. |
| **Institution info from `accounts_get`** | `item_public_token_exchange` does not return institution details. We populate them when fetching accounts. |
| **`_to_dict()` helper** | Plaid SDK can return model objects; we normalize to dict for consistent handling. |
