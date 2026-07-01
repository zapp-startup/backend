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
4. If the session is already `aal2`, Django finalizes the Django login and stores Supabase access/refresh tokens in the server session.
5. If the session is only `aal1`, Django stores a **pending MFA** auth block instead of finalizing the Django login.
6. Django returns only the minimal user/session summary to the browser.

## Request flow

- Frontend requests use `credentials: "include"`.
- Django authenticates browser requests with `SessionAuthentication` — the session cookie is the **sole API authentication path**.
- Supabase bearer tokens are **not** accepted as a request-authentication method. The `SupabaseJWTAuthentication` class still exists (it is exercised by unit tests and is the validation primitive), but it is no longer wired into `DEFAULT_AUTHENTICATION_CLASSES` or any view's `authentication_classes`. Reintroducing bearer request-auth should be treated as a security regression.
- The project-wide default is plain `SessionAuthentication`, which only resolves a fully logged-in Django session. A pending-MFA session is therefore not authenticated on general endpoints; only views that explicitly use `AuthSessionAuthentication` can resolve it.
- Banking and other sensitive flows read MFA/assurance state from the Django session first.

## MFA flow

1. Frontend calls backend MFA endpoints.
2. Django uses the stored Supabase access token to call Supabase MFA APIs server-to-server.
3. During signup/login, a pending-MFA session can access the MFA, `/me`, assurance, and onboarding/profile endpoints (so the signup survey can be completed alongside MFA setup), but **not** financial or banking data. Financial viewsets require a fully authenticated session (plain `SessionAuthentication` plus the `IsFullyAuthenticated` permission).
4. Django updates the local session assurance state:
   - `aal`
   - `mfa_factor_count`
   - `last_step_up_at`
5. Once MFA verify succeeds, Django finalizes the first-party session and normal authenticated API access resumes.
6. Banking policy enforcement uses the session assurance state plus freshness checks.

## Security boundaries

- The browser never directly sends Supabase bearer tokens to the first-party backend.
- CSRF protection is required for unsafe browser requests.
- Session cookie is `HttpOnly`; CSRF cookie remains readable by frontend JS for header injection.
- Supabase anon key in the frontend is public configuration, not a privileged secret.

## Non-goals

- This repo does not use the frontend Supabase client as the authoritative session owner.
- Any future auth change that introduces browser-managed access or refresh tokens for first-party APIs should be treated as a security regression unless explicitly reviewed.
