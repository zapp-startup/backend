# Zapp Backend

Primary Django REST backend for the Zapp AI integration milestone.

This repository is the main submission repo for the local Django integration assignment. It contains the authenticated API layer, valuation persistence models, AI chat entry points, and the backend services that the frontend uses to surface personalized value-score recommendations.

## Project Overview

The milestone feature delivered in this repo is a personalized valuation workflow for subscriptions and one-off purchases:

- Django stores subscription and item valuation outputs in the `apps.valuations` app.
- Authenticated REST endpoints expose valuation history to the frontend.
- The frontend surfaces value scores, recommendations, and evidence on the subscriptions and analytics experiences.
- The sibling [`value_score_model`](https://github.com/zapp-startup/value_score_model) repo contains the reusable training and scoring package that supports the value-score logic and future production scoring integration.

## Tech Stack

- Python 3.11+
- Django 6
- Django REST Framework
- SQLite for local development
- OpenAI API support for chat flows
- Supabase-backed authentication support

## Repository Links

- Primary submission repo: [backend](https://github.com/zapp-startup/backend)
- Supporting AI model repo: [value_score_model](https://github.com/zapp-startup/value_score_model)

## Project Layout

```text
apps/       Django apps: ai, banking, compliance, integrations, subscriptions, transactions, users, valuations, waitlist
config/     Django project wiring, settings, and operational config such as security alert rules
core/       Shared backend code used across apps, including encryption helpers
.docs/      Project notes, AI documentation, runbooks, and submission docs
scripts/    Developer utility scripts
```

The old top-level Django app packages were consolidated under `apps/`. The old `zapp/` project package was collapsed into `config/`, production validation lives under `config/security/`, and shared encryption code lives under `core/security/`.

## Local Setup

### 1. Create and activate a virtual environment

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

### 2. Install dependencies

```powershell
pip install --upgrade pip
pip install -r requirements.txt
```

### 3. Configure environment variables

Copy `.env.example` to `.env` and fill in the values you need for local development.

```powershell
Copy-Item .env.example .env
```

At minimum, local development needs:

- `SECRET_KEY`
- `DATABASE_URL`
- `OPENAI_API_KEY` if you want to use OpenAI-backed chat flows

Optional integrations such as Supabase, Plaid, and Spotify can be configured through the remaining variables in `.env.example`.

### 4. Run database migrations

```powershell
python manage.py migrate --settings=config.settings.development
```

### 5. Start the Django development server

```powershell
python manage.py runserver --settings=config.settings.development
```

The API is then available at `http://127.0.0.1:8000/`.

## How To Access The AI Feature

There are two practical ways to access the feature locally.

### Option A: Through the frontend

Start the sibling frontend repo and open the app in the browser:

```powershell
cd ..\frontend
npm install
npm run dev
```

The frontend runs on `http://127.0.0.1:5173/` by default.

Relevant UI entry points already wired to the backend:

- `/subscriptions`
  shows subscription cards, value-score presentation, recommendation text, and evidence details from stored subscription valuations
- `/analytics`
  includes the valuations experience and can be opened directly as `/analytics?tab=valuations`
- ZappBot quick actions
  normalize `/valuations/new` to `/analytics?tab=valuations&create=item`

### Option B: Directly through the API

The current public valuation interfaces are:

- `GET/POST /api/valuation-model-versions/`
- `GET/POST /api/subscription-valuations/`
- `GET/POST /api/item-valuations/`

Authenticated reads are filtered to the current user in the Django viewsets.

For local API-only testing, the repo also includes AI module testing notes in [`apps/ai/README.md`](apps/ai/README.md).

## AI Integration Scope In This Repo

This repo contains the Django-side integration layer:

- `apps.valuations.models`
  stores versioned valuation outputs, confidence, and encrypted evidence payloads
- `apps.valuations.views`
  exposes authenticated CRUD endpoints for valuation records
- `config.urls`
  registers the valuation endpoints under `/api/`
- `config.settings`
  contains environment-specific Django settings
- `config.security`
  contains production-security validation helpers
- `core.security`
  contains shared encryption helpers
- frontend consumers in the sibling `frontend` repo read these valuation records and display them in the subscriptions and analytics pages

The supporting `value_score_model` repo provides:

- feature engineering for subscription and user signals
- a three-tier scoring approach for sparse, medium, and dense histories
- evidence payload generation
- local training and batch scoring scripts

Important accuracy note:
the current backend repo stores and serves valuation outputs, but this README does not claim a direct runtime import path from Django into the `value_score_model` package unless you add that linkage explicitly in application code.

## Model Download And Artifact Notes

This repo is intentionally kept runnable without committing model weights or generated binary artifacts.

- No large model weights are stored in this backend repository.
- If you experiment with the sibling `value_score_model` package, train or score locally and keep generated artifacts local only.
- Downloaded weights, checkpoints, caches, and generated outputs should stay ignored by Git.

Typical local-only artifacts include:

- `.pt`
- `.bin`
- `.safetensors`
- checkpoint folders
- cache folders
- generated outputs such as evaluation files or batch score exports

## Supporting Model Package Workflow

If you want to reproduce the supporting value-score logic locally:

```powershell
cd ..
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r value_score_model/requirements.txt
python -m value_score_model.train --synthetic --n-users 50 --n-merchants 20
```

That package also supports batch scoring and local checkpoint generation. Submission-facing documentation for the AI feature still lives in this backend repo.

## Common Local Notes

- Always run the backend with `--settings=config.settings.development` for local work.
- The development settings expect loopback hosts such as `127.0.0.1`.
- OpenAI-backed chat features require a valid `OPENAI_API_KEY`.
- The frontend and backend should use the same loopback family so auth cookies and OAuth callbacks behave consistently.
- Security alert rules are stored in `config/security_alert_rules.json`.
- Security alert runbooks are stored in `.docs/runbooks/`.

## Deliverables Checklist

This repo now covers the assignment-facing repository deliverables:

- `README.md`
- `.docs/README_AI.md`
- `requirements.txt`
- Git hygiene rules for local model artifacts
- `.docs/CANVAS_SUBMISSION.md`
