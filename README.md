# Yulhaverse

A Django web app for tracking anime and manga lists (inspired by AniList-style workflows).

## Current features

- User signup/login/logout
- Browse `Anime` and `Manga` catalogs
- AniList API integration (top popular + search)
- Media detail pages:
  - `/anime/<id>/`
  - `/manga/<id>/`
- Add/update entries in personal list
- Library management: status, progress, rating, notes
- Cached fallback behavior if AniList is unavailable

## Tech stack

- Python + Django
- SQLite (default local DB)
- Optional PostgreSQL via environment variables
- AniList GraphQL API for catalog metadata

## Prerequisites

- Python 3.12+ (3.13/3.14 also work)
- [uv](https://docs.astral.sh/uv/) package manager

## Local setup (recommended with `uv`)

From the project root:

```bash
cd betteranilist
uv venv
```

Activate the virtual environment:

- Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

- macOS/Linux:

```bash
source .venv/bin/activate
```

Install dependencies:

```bash
uv pip install -r requirements.txt
```

## Database configuration

By default, the app uses local SQLite (`db.sqlite3`) with no extra setup.

Optional PostgreSQL:

1. Copy `.env.example` values to your shell environment.
2. Set `DB_ENGINE=postgresql` and provide `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`.

PowerShell example:

```powershell
$env:DB_ENGINE="postgresql"
$env:DB_NAME="betteranilist"
$env:DB_USER="postgres"
$env:DB_PASSWORD="your_password_here"
$env:DB_HOST="127.0.0.1"
$env:DB_PORT="5432"
```

## Run the project

Apply migrations:

```bash
python manage.py migrate
```

Create an admin account (optional):

```bash
python manage.py createsuperuser
```

Start development server:

```bash
python manage.py runserver
```

Open:

- App: <http://127.0.0.1:8000/>
- Admin: <http://127.0.0.1:8000/admin/>

## Quick quality checks (`ruff`)

Install ruff in the project environment:

```bash
uv pip install ruff
```

Run lint checks:

```bash
ruff check .
```

Format (optional):

```bash
ruff format .
```

## Useful routes

- `/` home dashboard
- `/anime/` anime catalog
- `/manga/` manga catalog
- `/library/` user library
- `/signup/` account creation
- `/login/` login

## Notes

- AniList data is fetched via API and cached in local catalog entries.
- If AniList is temporarily unavailable, cached data is shown when possible.
