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

## Supabase auth user sync flow

When the frontend logs in with Supabase, include the Supabase access token on backend
requests:

```http
Authorization: Bearer <supabase_access_token>
```

Backend behavior:
- validates the token via Supabase JWKS;
- reads the `sub` claim as the Supabase user id;
- creates or links a Django `users.User` row (`supabase_uid`) on first request.

Recommended first call after frontend login:

```bash
POST /api/auth/sync/
```

This endpoint confirms the backend user record exists and returns the mapped user payload.

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
