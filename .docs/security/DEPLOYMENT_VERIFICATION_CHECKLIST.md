# Deployment Verification Checklist

This checklist must be completed manually for production sign-off. These controls are **not fully verifiable from repository code alone**.

## 1. Edge TLS

Verify:

- app frontend is served only over HTTPS
- API is served only over HTTPS
- TLS policy is TLS 1.2 or newer
- HSTS is present on production responses
- certificates are valid and auto-renewed

Evidence to capture:

- load balancer/CDN TLS policy screenshot
- browser network capture showing only `https://` traffic to app, API, and Supabase
- external TLS scan or managed-hosting TLS export

## 2. Database and backups

Verify:

- Postgres/Supabase storage encryption at rest is enabled
- automated backups are encrypted
- snapshot access is restricted
- replica access is restricted

Evidence to capture:

- provider setting screenshot or export
- backup policy screenshot
- IAM/access-control review record

## 3. Session storage

Verify:

- Django session backend in production
- where session data is stored
- encryption/protection of that store
- retention/TTL behavior
- access controls for operators and support staff

Evidence to capture:

- deployment config showing session backend
- session store platform settings
- retention screenshot or config export

## 4. Secrets

Verify:

- `SECRET_KEY` stored only in secret manager / deployment env
- `SUPABASE_*` secrets stored only in secret manager / deployment env
- `PLAID_*` secrets stored only in secret manager / deployment env
- Plaid token encryption keys are present in production
- key rotation procedure exists and is documented

Evidence to capture:

- CI/CD secret manager screenshots
- runbook or rotation SOP

## 5. Supabase MFA policy

Verify:

- MFA is enabled in the Supabase project
- TOTP factor enrollment is enabled
- recovery / account recovery process is documented
- JWT/session claims show expected `aal` behavior after step-up

Evidence to capture:

- Supabase Auth MFA settings screenshot
- sample redacted JWT/session evidence after step-up
- sign-in test notes

## 6. Operational sign-off

Required reviewers:

- backend owner
- security owner
- ops/platform owner

Final rule:

- Production is not considered fully signed off until every section above has recorded evidence.
