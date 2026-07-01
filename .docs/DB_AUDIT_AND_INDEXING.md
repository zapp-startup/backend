# Zapp Database Audit & Indexing Plan

> Generated 2026-06-29 against the live Supabase Postgres (`PostgreSQL 17.6`,
> `aws-0-us-west-2.pooler.supabase.com`, database `postgres`).
> All diagnostics were **read-only** (catalog / `pg_stat_*` / `information_schema`).
> Schema changes are authored as **Django model changes → migrations**, then applied
> over the **session pooler (port 5432)**, never the transaction pooler (6543).

## Context

- Supabase is used here as an **auth provider only**. App data lives in 50 Django-owned
  tables in `public`; Supabase manages `auth.*`, `storage.*`, `realtime.*`, `vault.*`.
- Data is small (≈20 users) but **cumulative index-scan stats show real traffic** — so
  `idx_scan = 0` is a reliable "unused" signal, not a cold-DB artifact.
- The schema is **over-indexed, not under-indexed**: total index bytes exceed table bytes
  on most tables. The work is mostly *removal*.

---

## 0. Scoreboard

| Area | Status |
|------|--------|
| Migrations applied (93) / drift | ✅ clean (`migrate --check`, `makemigrations --check` both clean) |
| Missing FK indexes | ✅ none |
| Invalid indexes | ✅ none |
| RLS / API exposure | ⚠️ RLS off on all 50 tables (safe today only by absence of grants) |
| Redundant indexes (exact dup) | 🔴 11 confirmed |
| `compliance_auditevent` over-indexing | 🔴 ~12 indexes, 0 scans, index > table size |
| Unused low-value indexes | 🟡 ~15 (verify admin usage first) |
| Stale planner stats | 🟡 27 tables never `ANALYZE`d |
| Hot-loop / N+1 smell | 🟡 several pkeys with 1.6M–3.6M scans on 20 users |
| Connection string uses txn pooler for DDL | 🟡 use 5432 for migrations |

---

## 1. Healthy — no action

- [x] Migrations: 93 applied, nothing unapplied, models in sync with migration files.
- [x] Every foreign key has a covering index (Django default).
- [x] No invalid/`INVALID` indexes, no duplicate **unique** constraints.
- [x] Supabase API roles (`anon`, `authenticated`) hold **zero table grants** → Django
      tables are not readable through the auto REST/GraphQL API.

---

## 2. Issue checklist

### 2A. Security / Supabase hardening — config-level (no code)

- [ ] **RLS disabled on all 50 `public` tables.** Currently not exploitable (no grants),
      but "safe by absence." Supabase's linter flags every table as `rls_disabled_in_public`.
      *Decision needed:* either (a) keep `public` out of the PostgREST exposed-schema list
      and document that the API is intentionally Django-only, or (b) enable RLS + a default
      `deny` as defense-in-depth.
- [ ] **`anon` / `authenticated` have `CREATE` on schema `public`.** Tighten:
      ```sql
      REVOKE CREATE ON SCHEMA public FROM anon, authenticated;
      ```
- [ ] Confirm `public` is **not** in Supabase → Project Settings → API → "Exposed schemas"
      (if it is, the only thing protecting the tables is the missing grants).

### 2B. Redundant indexes — exact duplicates (safe drop, zero query loss)

Each is a non-unique index whose columns + operator class exactly match an existing
(usually unique) index. Remove the duplicate `models.Index(...)` from the model `Meta`.

- [ ] `ai_userfact.ai_userfact_user_id_9575a5_idx` → covered by `unique_together(user, fact_key)` — `apps/ai/models.py`
- [ ] `users_userpreference.users_userp_user_id_0c4075_idx` → covered by `uniq_user_preference_key` — `apps/users/models.py`
- [ ] `subscriptions_merchant.subscriptio_name_554356_idx` → covered by unique `name` — `apps/subscriptions/models.py`
- [ ] `banking_bankaccount.banking_ban_connect_660465_idx` → covered by `uniq_connection_plaid_account` — `apps/banking/models.py`
- [ ] `gamification_badge.gamificatio_code_edd252_idx` → covered by unique `code`
- [ ] `gamification_group.gamificatio_invite__7654b3_idx` → covered by unique `invite_code`
- [ ] `gamification_groupinvite.gamificatio_invite__daf920_idx` → covered by unique `invite_code`
- [ ] `gamification_periodicreview.gamificatio_user_id_52c3f3_idx` → covered by `uniq_user_periodic_review`
- [ ] `gamification_pointevent.gamificatio_event_k_b535f9_idx` → covered by unique `event_key`
- [ ] `valuations_valuationmodelversion.valuations__name_9fb73c_idx` → covered by unique `(name, version)`
- [ ] `integrations_spotifyconnection`: **two** identical non-unique indexes on `spotify_user_id`
      (`integration_spotify_5e8802_idx` and `..._91ca57dc`). Keep at most one — and since
      `spotify_user_id` is never filtered in code (0 scans), preferably **drop both**.

### 2C. `compliance_auditevent` over-indexing — biggest storage/write win

17 indexes; index bytes (1.24 MB) > table bytes on a 2.4k-row append-heavy audit log.
Six single-column `CharField`s carry `db_index=True` (`apps/compliance/models.py`), each
generating a btree **and** a `_like` index — **all with 0 scans**, and none are filtered as
single columns anywhere in the code:

- [ ] Remove `db_index=True` from `event_name` (drops 2 idx, ~112 kB)
- [ ] Remove `db_index=True` from `outcome` (~96 kB)
- [ ] Remove `db_index=True` from `actor_type` (~96 kB)
- [ ] Remove `db_index=True` from `source_system` (~96 kB)
- [ ] Remove `db_index=True` from `request_id` (~368 kB — largest)
- [ ] Remove `db_index=True` from `resource_type` (~112 kB; also covered by the composite `(resource_type, resource_id)`)
- [ ] **Keep**: `(user, occurred_at)`, `(resource_type, resource_id)`, standalone `occurred_at`
      (used by `apps/compliance/monitoring.py` global time-window scan).

### 2D. Unused low-value indexes — verify admin usage, then drop (phase 2)

0 scans, low cardinality (enum-ish). Often only ever used by Django admin list filters —
**check `admin.py` before dropping.**

- [ ] `users_usercomputed`: `product_spending_style`, `spending_personality`, `subscription_behavior_type` (3 × 32 kB)
- [ ] `users_userrawexplicit`: `budget_style`, `financial_goal`, `income_range`, `risk_tolerance` (4)
- [ ] `users_userrawinferred`: `(window_days, computed_at)`
- [ ] `banking_banktransaction`: `zapp_primary_category` (`db_index` → btree + `_like`, 0 scans)
- [ ] `subscriptions_merchant`: `category`
- [ ] `valuations_itemvaluation`: `(item_name, created_at)`, `recommendation`
- [ ] `valuations_subscriptionvaluation`: `recommendation`, `(user_id, subscription_id, period_end)`
- [ ] `valuations_transactionvaluation`: all four composites are 0-scan (table near-empty; revisit when populated)

### 2E. Prefix-redundant single-column FK indexes — optional (phase 3)

A single-column FK index is redundant when a composite index **leading with that same
column** exists. Removable via `db_index=False` on the FK field. Low urgency (16 kB each),
list for completeness:

- [ ] `banking_banktransaction.account_id` (covered by `(account_id, date)`)
- [ ] `compliance_auditevent.user_id` (covered by `(user_id, occurred_at)`)
- [ ] `ai_conversationmemoryitem.conversation_id`, `user_id` (covered by their composites)
- [ ] `gamification_pointevent.user_id` / `group_id` (covered by `(user_id, action, created_at)` etc.)
- [ ] (apply the same rule wherever a `(<fk>, …)` composite already exists)

### 2F. Maintenance — stale statistics

- [ ] **27 tables have never been `ANALYZE`d** → the planner has no stats. Run a one-time
      `ANALYZE;` (safe, no locks). Optionally `VACUUM (ANALYZE);`. Confirm Supabase autovacuum
      is enabled going forward.

### 2G. Application efficiency — hot-loop / N+1 smell (code, not indexes)

On a 20-user DB these scan counts indicate row-by-row lookups in loops, not index problems:

- [ ] `users_user_pkey` — 3,584,034 scans
- [ ] `valuations_itemvaluation_pkey` — 2,830,148 scans
- [ ] `ai_conversation.linked_item_valuation_id` — 2,768,630 scans (investigate why an AI
      conversation FK is probed millions of times)
- [ ] `transactions_transaction_pkey` — 1,616,791 scans
- [ ] `transactions_transactionreflection.(transaction_id, reflected_at)` — 1,616,579 scans
- [ ] `valuations_valuationmodelversion_pkey` — 1,600,401 scans (cache the active model version
      instead of re-fetching per row)
      → Audit the relevant services for missing `select_related` / `prefetch_related` /
        bulk fetches.

### 2H. Operational

- [ ] `DATABASE_URL` points at the **transaction pooler (6543)**. Fine for the app; use the
      **session pooler (5432)** or a direct connection for migrations/DDL.
- [ ] `frontend/src/api/supabaseClient.ts` hardcodes a local-CLI anon key fallback — harmless
      (local demo key) but confirm prod always supplies `VITE_SUPABASE_*`.

---

## 3. Indexing strategy — how it *should* happen

### Principles

1. **Index the query, not the column.** Build composites that match real `WHERE` + `ORDER BY`:
   equality columns first, the range/sort column last (e.g. `(user_id, occurred_at)` serves
   `WHERE user_id = ? ORDER BY occurred_at`).
2. **A composite leading with column C covers single-column lookups on C** — so a separate
   index on just C is redundant.
3. **A non-unique index on the exact columns of a unique constraint is always redundant** —
   the unique index serves the same reads.
4. **Low-cardinality columns (enums, booleans) rarely benefit from a plain b-tree.** Prefer a
   composite (`(user_id, status)`) or a **partial index** (`WHERE status = 'active'`).
5. **Every `db_index=True` on a `CharField` costs two indexes in Django** (b-tree +
   `varchar_pattern_ops` `_like`). Only set it where you actually filter/sort by that column.
6. **Django auto-indexes every FK.** Keep it unless a leading composite already covers it.
7. **Match sort direction** or rely on backward index scans; encode direction only for mixed
   `ASC/DESC` sorts.

### What this schema already does well

- Per-user composites that match the dominant access pattern exist and are used:
  `transactions_transaction (user_id, occurred_at)` 1,439 scans,
  `(merchant_id, occurred_at)` 10,475 scans;
  `subscriptions_subscription` partial unique `(user_id, merchant_id) WHERE status='active'` 801 scans;
  `compliance_financialconsent (user_id, accepted_at)` 412 scans.
- Good use of a **partial unique** for "one active subscription per user/merchant" and for
  `ai_conversationmemoryitem (user_id, dedupe_key) WHERE dedupe_key IS NOT NULL`.

### Additions to consider (only if profiling shows need — schema is over-indexed)

- `transactions_transaction (user_id, direction, occurred_at)` — `direction='spend'` filtering is
  hot in `feedback_candidates.py` and valuations; today they ride `(user_id, occurred_at)` and
  filter `direction` in-heap. Low cardinality, so **measure before adding**.
- Partial index `WHERE stale_at IS NULL` for the valuation "latest active" lookups in
  `valuations/services/persistence.py`, if those tables grow.

> Net: **remove first, add almost nothing.** The right end state for each table is the
> primary key + the unique constraints + the 1–3 per-user composites that match real queries,
> and not much else.

---

## 4. Execution plan

**Phase 1 — zero-risk (recommended first):**
1. `ANALYZE;` (2F) — instant planner improvement, no schema change.
2. Drop the 11 exact-duplicate indexes (2B) — model edits → one migration.
3. Trim `compliance_auditevent` `db_index` flags (2C) — same migration.

**Phase 2 — after a quick `admin.py` check:**
4. Drop unused low-cardinality indexes (2D).

**Phase 3 — optional polish:**
5. Prefix-redundant FK indexes (2E) via `db_index=False`.

**Separate track (code, not DB):**
6. Investigate the N+1 hot loops (2G).
7. Apply the Supabase security hardening (2A) in the dashboard/SQL editor.

**Applying migrations:** generate with `makemigrations`, review the `RemoveIndex` /
`AlterField` operations, then `migrate` over the **5432 session pooler** (swap the port on the
existing pooler host; same `postgres.<project-ref>` credentials).
