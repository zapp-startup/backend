# Security & Plaid readiness — control map

This document maps **implemented** backend controls to evidence and questionnaire notes. Items that require **dashboard, infra, or vendor** action are listed separately.

## 1. MFA / strong auth for bank linking

| Item | Location | Evidence |
|------|----------|----------|
| JWT `aal` / factor hints in auth context | `users/supabase_auth.py` | Log sample JWT claims (redacted) showing `aal` after MFA challenge |
| Policy: require `aal2` in production | `users/security_assurance.py`, `zapp/settings/production.py` `BANKING_REQUIRE_MFA` | Screenshot of 403 with `mfa_not_enrolled` / `mfa_verification_needed` when `aal1` |
| Frontend assurance API | `GET /api/security/auth-assurance/` | API response JSON |

**Assumption:** Supabase issues `aal` in access tokens per [Supabase Auth MFA](https://supabase.com/docs/guides/auth/auth-mfa). Verify in your project’s JWT payload; if `aal` is absent, enable MFA and test, or add a Supabase hook to enrich sessions.

**Manual / vendor:** Enroll MFA in Supabase project; configure recovery codes; document RPO/RTO outside this repo.

---

## 2. Consent logging

| Item | Location | Evidence |
|------|----------|----------|
| Immutable consent rows | `compliance.models.FinancialConsent` | DB export of table; migration `compliance/0001_*.py` |
| Record API | `POST /api/compliance/consent/` | Request/response logs (no PII in logs beyond user id) |
| Dedup window | `compliance/services.py` | Code review |

**Manual:** Legal review of consent text; hosted policy URL matches `PRIVACY_POLICY_URL`.

---

## 3. Privacy policy metadata

| Item | Location | Evidence |
|------|----------|----------|
| Config | `PRIVACY_POLICY_*` in `zapp/settings/base.py` | Env vars in deployment |
| Public metadata | `GET /api/compliance/privacy-policy/` | Response JSON |

Consent validity ties to `PRIVACY_POLICY_VERSION` — users must re-consent when version bumps.

---

## 4. HTTPS / production transport

| Item | Location | Evidence |
|------|----------|----------|
| SSL redirect, secure cookies, HSTS | `zapp/settings/production.py` | Infra screenshot: TLS 1.2+ at load balancer |
| Startup validation | `zapp/security/production_validation.py` | Failed deploy logs if misconfigured |
| Proxy header | `SECURE_PROXY_SSL_HEADER` | Nginx/ALB config sets `X-Forwarded-Proto: https` |
| Frontend production URL validation | `frontend/src/config/apiEnv.ts`, `frontend/src/api/client.ts`, `frontend/src/api/ai.api.ts`, `frontend/src/api/supabaseClient.ts` | Production build/runtime error if API or Supabase URLs are not HTTPS |

Questionnaire item 12 ("encrypt data-in-transit between clients and servers using TLS 1.2 or better") maps to this control. Answer **Yes** only when:

- frontend hosting serves the app over HTTPS;
- the public API domain terminates TLS 1.2+ at the managed edge/load balancer;
- browser-to-Supabase traffic uses the hosted `https://<project-ref>.supabase.co` origin; and
- production `VITE_API_URL` / `VITE_SUPABASE_URL` point only to HTTPS origins.

**Manual:** TLS certificates, cipher suites, and penetration test of TLS — outside app code.

**Evidence set:** managed-hosting or load-balancer TLS policy screenshot, browser network screenshot showing only `https://` requests to app/API/Supabase, and one external TLS scan or platform TLS settings export.

---

## 5. Plaid token protection

| Item | Location | Evidence |
|------|----------|----------|
| Tokens never in serializers | `banking/serializers.py` | Code review |
| Fernet encryption at rest | `banking/token_storage.py`, `PLAID_TOKEN_ENCRYPTION_KEY` | Env in prod; decrypt path in tests |
| Legacy token backfill | `python manage.py encrypt_plaid_access_tokens` | Command output showing plaintext rows upgraded |
| Admin hides token | `banking/admin.py` | Django admin screenshot |

Production expectation: Plaid access tokens are stored encrypted at the app layer, never returned by serializers, and existing plaintext legacy rows are backfilled with `encrypt_plaid_access_tokens`.

**Manual:** Supabase/Postgres encryption at rest, disk encryption, backups — provider-managed.

---

## 6. Deletion / retention

| Item | Location | Evidence |
|------|----------|----------|
| Purge service | `banking/lifecycle.py` `purge_user_bank_data` | Run log from `purge_user_bank_data` command |
| Command | `python manage.py purge_user_bank_data --user-id N` | CLI output |

**Manual:** Legal hold, backup retention, Plaid item removal via Plaid API if required by agreement — review with counsel.

---

## 7. Rate limiting

| Item | Location | Evidence |
|------|----------|----------|
| Throttle scopes | `banking/throttles.py`, `compliance/throttles.py` | `DEFAULT_THROTTLE_RATES` in `zapp/settings/base.py` |
| Applied views | `banking/views.py`, `compliance/views.py` | 429 responses under load test |

---

## 8. Audit logging

| Item | Location | Evidence |
|------|----------|----------|
| Structured events | `banking/views.py`, `compliance/services.py`, `banking/security_checks.py` | Log aggregation query (no secrets) |

Secrets, raw Plaid payloads, and full account numbers are **not** logged.

---

## 9. Dev-only auth

| Item | Location | Evidence |
|------|----------|----------|
| `X-Dev-User` disabled in prod | `users/dev_auth.py` + `DEBUG` / `ALLOW_DEV_HEADER_AUTH` | `production.py` sets `ALLOW_DEV_HEADER_AUTH = False` |

---

## 10. Dependency / SDLC automation

| Item | Location | Evidence |
|------|----------|----------|
| Targeted automated tests | `users/tests_auth_assurance.py`, `compliance/tests.py`, `banking/tests.py`, frontend Vitest suites | Local test run output or external CI |
| CI / dependency scanning | Not repo-confirmed in the current repos | External CI/security evidence if present |

**Manual:** SAST/DAST, pen test, Plaid security questionnaire final submission.

---

## Honest gaps (not fully enforced in code)

- **WAF / IP reputation:** Configure at CDN or load balancer.
- **Secrets rotation:** Operate via vault/CI, not Django alone.
- **Row-level audit of all reads:** Not implemented; only consent and banking security events.
- **Plaid dashboard** compliance settings: Manual configuration in Plaid Console.
