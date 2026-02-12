# Zapp Backend (Django REST)

Backend service for the Zapp project, built using **Django** and **Django REST Framework**.

---

## Tech Stack

- Python 3
- Django
- Django REST Framework
- SQLite (default, development)

---

## Prerequisites

Ensure the following are installed:

- Python 3.9+
- pip
- (Recommended) `virtualenv` via `python -m venv`

---

## Local Setup

### 1. Navigate to the backend folder

```bash
cd zapp-backend
```

python -m venv .venv
source .venv/bin/activate # macOS / Linux

# .venv\Scripts\activate # Windows (PowerShell)

# Setting up requirements in the virtual enviornemnt set up

pip install -r requirements.txt

## Database Setup

python manage.py migrate

## Running the Server (Development)

python manage.py runserver --settings=zapp.settings.development

API will be avaliable on -> http://127.0.0.1:8000/

## Project Settings

This project uses environment-specific settings.

zapp/settings/development.py

-> Make sure to always run the server with --settings=zapp.settings.development

## Git Workflow - Backend Repo

git branch
git branch -a

## Common Issues -> ensuring pip is up to date

pip install --upgrade pip

## How to create a branch -> backend

git status
git checkout -b branchname
git branch(to check branch)
git add .
git commit -m ""
git push -u origin branch name

## Supabase Authentication (Backend)

Supabase JWT authentication is supported via a custom DRF authentication class.

### Environment variables

Set these in your `.env`:

- `SUPABASE_PROJECT_URL=https://<project-ref>.supabase.co`
- `SUPABASE_JWT_ISSUER=https://<project-ref>.supabase.co/auth/v1`
- `SUPABASE_JWT_AUDIENCE=authenticated`
- `SUPABASE_JWT_ROLE=authenticated`
- `SUPABASE_JWT_JWKS_URL=https://<project-ref>.supabase.co/auth/v1/.well-known/jwks.json` (optional if `SUPABASE_PROJECT_URL` is set)

### Request format

Pass the Supabase access token in the `Authorization` header:

```http
Authorization: Bearer <supabase_access_token>
```

### What happens on each request

1. The backend reads the bearer token.
2. It fetches Supabase signing keys from the JWKS endpoint.
3. It verifies signature + audience (+ issuer when configured).
4. It maps token `sub` to a Django user (`username=sub`), creating the user on first request.
5. Authenticated requests can use normal DRF permissions.



### Is this OAuth with Supabase?

Short answer: **partially**.

- Supabase handles the OAuth flow (Google/Apple/etc.) and issues an access token after login.
- This backend acts as a **resource server**: it verifies the Supabase access token and authorizes API requests.
- So the backend is not running the OAuth redirect/code-exchange itself; Supabase does that part.

This is a standard split-architecture pattern for separate frontend/backend apps.

### Frontend in a separate repo/folder

This setup works when frontend and backend are separated. The backend only needs a valid Supabase bearer token and does not depend on frontend code location.

For cross-origin requests, set:

- `CORS_ALLOWED_ORIGINS=http://localhost:5173,https://your-frontend-domain.com`
- `CSRF_TRUSTED_ORIGINS=http://localhost:5173,https://your-frontend-domain.com`

### Session inspection endpoint

Use:

- `GET /api/auth/session/`

It returns the authenticated Django user plus decoded JWT claims.
