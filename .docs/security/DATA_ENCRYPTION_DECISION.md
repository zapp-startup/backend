# Data Encryption Decision

## Decision

We will **keep app-layer field encryption only for high-value secrets**, not for the main query-heavy profile and banking tables at this stage.

## What is encrypted at the app layer

- Plaid access tokens in `banking.BankConnection.plaid_access_token`
- Encryption mechanism: Fernet with rotation support via:
  - `PLAID_TOKEN_ENCRYPTION_KEY`
  - `PLAID_TOKEN_ENCRYPTION_KEYS`

## What is not encrypted at the app layer

- `users.UserRawExplicit` profile fields such as:
  - `dob`
  - `location_zip`
  - `income_range`
  - `monthly_income`
  - `monthly_fixed_expenses`
- `banking.BankAccount` and `banking.BankTransaction` business data such as:
  - balances
  - merchant names
  - transaction names
  - dates
  - categories
  - minimized `raw_payload`

## Rationale

- These fields are used for filtering, joins, analytics, and product logic.
- Retrofitting application-layer encryption to these columns would materially complicate:
  - querying
  - indexing
  - reporting
  - admin and support workflows
  - migration of existing data
- The highest-risk credential-like secret in scope is the Plaid access token, which is already app-layer encrypted.

## Required compensating controls

Because these fields remain plaintext at the application layer, production relies on:

- managed Postgres/Supabase encryption at rest
- encrypted backups
- tightly scoped database/admin access
- audited production access procedures
- secure session storage

## When to revisit this decision

Revisit field-level encryption if any of the following become true:

- the threat model includes DB snapshot compromise as a primary scenario
- regulators/customers require application-layer encryption for specific fields
- sensitive exports or analytics replicas broaden data exposure
- internal access to production data becomes hard to constrain operationally

## Current conclusion

- **Secrets**: encrypt at the app layer
- **Query-heavy financial/profile records**: keep plaintext in application storage, but only with verified infra encryption and access controls
