# ShoeRAG Web

Local forensic footwear retrieval: a React/TypeScript UI and a Django REST Framework API. Investigators add image folders, index them, and search by image or text. Matches sit on a case and can be rated, noted, or approved.

Images are stored once by SHA-256 digest (`ContentObject`). Each corpus listing is a separate `EvidenceFile`, so the same file can appear in two collections without duplicating bytes or vectors. Search uses PostgreSQL with pgvector and Celery. Request validation is DRF serializers at the API boundary, not Pydantic.

This is a local demo. No evidence images or private specs are in the repo. Seed passwords equal usernames. Use `127.0.0.1`, not `localhost`.

## Celery

Celery runs ingest, encode, search, and report jobs so the API does not block. Redis is the broker. Progress is stored in PostgreSQL (`TaskRun`), not a Celery result backend. Without the worker, those jobs queue and never finish.

## Requirements

- Docker Desktop
- Python 3.12+
- Node.js 20+
- Redis and PostgreSQL (via Compose below)

## Technology

| Technology | Why it is used |
| --- | --- |
| Django 5 | Auth, ORM, admin, and a support horizon for a maintainable API. |
| Django REST Framework | Serializers validate at the HTTP boundary; permissions are first-class. Not Pydantic — one contract with OpenAPI. |
| drf-spectacular | OpenAPI schema and `/api/v1/docs/`. |
| PostgreSQL 16 + pgvector | One database for cases, evidence, and nearest-neighbour search; filter in SQL. |
| Redis | Celery broker (and lockout state). |
| Celery | Background encode/search/reports; four queues: `default`, `encoding`, `reporting`, `maintenance`. |
| React + TypeScript + Vite | UI with typed screens; Vite proxies `/api` to Django. |
| TanStack Query | Polls task progress and search results without hand-rolled timers. |
| Tailwind CSS | Desktop-matched layout and tokens. |
| Docker Compose | Local Postgres and Redis on `127.0.0.1` only. |
| PyTorch / transformers (optional) | Real CLIP and DINOv2; `ml.txt`. Without it, stand-in encoders still run. |

## Install

```powershell
copy .env.example .env
docker compose up -d

python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r backend\requirements\dev.txt

cd backend
python manage.py migrate --database=migration
python manage.py seed_demo
```

On macOS/Linux use `cp .env.example .env`, `source .venv/bin/activate`, and `/` in paths.

## Run

Four processes. From the repo root, keep the venv active for Python terminals.

**API** (`http://127.0.0.1:8000`):

```powershell
cd backend
python manage.py runserver 127.0.0.1:8000
```

**Worker** (encode and search). On Windows add `--pool=solo`:

```powershell
cd backend
celery -A config worker -Q default,encoding,reporting,maintenance -l info --pool=solo
```

**UI** (`http://127.0.0.1:5173`):

```powershell
cd frontend
npm install
npm run dev
```

Open `http://127.0.0.1:5173/login`. Vite proxies `/api` to port 8000. Sign in with `investigator` / `investigator` (or `administrator` / `administrator`).

API docs (after login): `http://127.0.0.1:8000/api/v1/docs/`.

Optional neural encoders: `pip install -r backend/requirements/ml.txt` (large download). Without it, search still runs on stand-in encoders.

## Stop

Ctrl+C the API, worker, and Vite terminals, then `docker compose stop`.
