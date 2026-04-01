# Auth Architecture

This application uses a **backend-for-frontend (BFF)** authentication model.

## Source of truth

- **Django session is the only authoritative application session.**
- The browser stores:
  - Django session cookie
  - Django CSRF cookie
- The browser does **not** store first-party API bearer tokens in `localStorage` or `sessionStorage`.
- Supabase access and refresh tokens are stored only inside the server-side Django session block.

## Login flow

1. Frontend calls `POST /api/auth/login/` with credentials and CSRF.
2. Django calls Supabase Auth server-to-server.
3. Django validates the returned user identity and creates/links the local user.
4. Django stores Supabase access/refresh tokens in the server session.
5. Django returns only the minimal user/session summary to the browser.

## Request flow

- Frontend requests use `credentials: "include"`.
- Django authenticates browser requests with `SessionAuthentication`.
- For legacy compatibility, `SupabaseJWTAuthentication` still exists, but session auth is the preferred browser path.
- Banking and other sensitive flows read MFA/assurance state from the Django session first.

## MFA flow

1. Frontend calls backend MFA endpoints.
2. Django uses the stored Supabase access token to call Supabase MFA APIs server-to-server.
3. Django updates the local session assurance state:
   - `aal`
   - `mfa_factor_count`
   - `last_step_up_at`
4. Banking policy enforcement uses the session assurance state plus freshness checks.

## Security boundaries

- The browser never directly sends Supabase bearer tokens to the first-party backend.
- CSRF protection is required for unsafe browser requests.
- Session cookie is `HttpOnly`; CSRF cookie remains readable by frontend JS for header injection.
- Supabase anon key in the frontend is public configuration, not a privileged secret.

## Non-goals

- This repo does not use the frontend Supabase client as the authoritative session owner.
- Any future auth change that introduces browser-managed access or refresh tokens for first-party APIs should be treated as a security regression unless explicitly reviewed.
