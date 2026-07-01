# Feedback Candidate Selection

This document describes the feedback candidate selection system that determines which transactions the app should ask users for feedback about.

## Endpoint

```
GET /api/transactions/feedback-candidates/
```

**Query parameters:**
- `n` (optional): Number of candidates to return (default: 2, max: 10)
- `days` (optional): Time window in days (default: 90, max: 365)

**Response:**
```json
[
  { "transaction_id": 1042 },
  { "transaction_id": 873 }
]
```

**Authentication:** Requires `IsAuthenticated` (same as other transaction endpoints).

## Eligibility Filters

Only transactions that meet **all** of the following are considered:

1. **User ownership** – Belongs to the requesting user
2. **No existing feedback** – `satisfaction_rating`, `regret_rating`, `repurchase_likelihood`, and `reflection_text` are all null/empty
3. **Spend direction** – `direction = "spend"` (excludes income and refunds)
4. **Time window** – `occurred_at` within the last N days (default 90)
5. **Excluded categories** – Groceries and Bills are excluded entirely (very low signal)

## Scoring Formula

Each eligible transaction receives a priority score:

```
score =
  0.35 × category_weight
+ 0.25 × amount_score
+ 0.20 × recency_score
+ 0.10 × novelty_score
+ 0.10 × uncertainty_score
- 0.25 × routine_penalty
```

### Scoring Factors

| Factor | Weight | Description |
|--------|--------|-------------|
| **category_weight** | 0.35 | High-signal categories (restaurants, shopping, entertainment, subscriptions) score 0.85–0.95. Low-signal (groceries, bills) are excluded. |
| **amount_score** | 0.25 | Larger purchases provide more insight. Normalized relative to user's typical transaction size. 3× average = max score. |
| **recency_score** | 0.20 | Recent transactions are easier to remember. Exponential decay: 1.0 at 0 days, ~0.37 at 30 days. |
| **novelty_score** | 0.10 | Prioritizes merchants/categories where the user has little prior feedback. |
| **uncertainty_score** | 0.10 | Placeholder: unlinked merchants score higher (we know less about them). |
| **routine_penalty** | -0.25 | Known subscriptions and routine categories are penalized. |

### Category Weights (TransactionCategory)

| Category | Weight | Notes |
|----------|--------|-------|
| Subscriptions | 0.95 | High signal |
| Eating Out | 0.90 | Restaurants, dining |
| Entertainment | 0.90 | High signal |
| Shopping | 0.85 | Retail, discretionary |
| Education | 0.70 | Medium-high |
| Transport | 0.50 | Mixed (ride-share vs routine gas) |
| Other | 0.55 | Default |
| Health | 0.40 | Often routine |
| Groceries | 0.10 | Excluded from candidates |
| Bills | 0.05 | Excluded from candidates |

## Diversity Rule

After sorting by score, a diversity filter is applied so we do not return two nearly identical transactions:

- At most one transaction per **merchant**
- At most one transaction per **category**

This ensures variety in the feedback requests.

## Assumptions

1. **Transaction model** – Uses `transactions.Transaction` (user feedback ledger), not `banking.BankTransaction`. Feedback fields (`satisfaction_rating`, etc.) live on `Transaction`.

2. **Category mapping** – Uses `TransactionCategory` enum: subscriptions, groceries, eating_out, transport, shopping, bills, entertainment, health, education, other. If you later integrate `BankTransaction` (with `ZappPrimaryCategory`/`ZappSubcategory`), a mapping layer would be needed.

3. **Routine detection** – Uses `subscription` FK for known subscription charges. `Transaction` has no behavioral tags; `BankTransaction` has `TransactionBehavioralTag` (Recurring, Essential, etc.) which could be used if/when feedback is added there.

4. **Uncertainty** – Currently a simple heuristic (no merchant = higher uncertainty). A future ML model could provide real confidence scores.

## Modified Files

- `transactions/feedback_candidates.py` – New module: scoring logic and candidate selection
- `transactions/views.py` – Added `feedback_candidates` action to `TransactionViewSet`
- `transactions/FEEDBACK_CANDIDATES.md` – This documentation
