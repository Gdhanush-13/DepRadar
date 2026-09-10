# DepRadar

Codecov for dependency health: scan a public GitHub repository, score dependency freshness and risk, and embed the result as a live SVG badge.

Live demo: configure `APP_BASE_URL` after deployment. API docs are available at `/docs`.

![DepRadar badge](http://localhost:8000/badge/encode/encode.svg)

## Run locally

```bash
python -m venv .venv
.venv\\Scripts\\activate       # Windows
pip install -e ".[dev]"
uvicorn app.main:app --reload
```

Or use the full stack: `docker compose up --build`.

Architecture: browser (Jinja2 + HTMX + Alpine) → FastAPI → SQLAlchemy (SQLite locally / PostgreSQL in production) and Redis/Celery workers. External adapters call GitHub, PyPI, npm, and OSV.dev.

## Configuration

Copy `.env.example` to `.env`. GitHub OAuth is optional for public one-off scans. Set `DATABASE_URL`, `REDIS_URL`, `JWT_SECRET`, `INTERNAL_RESCAN_KEY`, and `GITHUB_TOKEN` in production. Render deploys automatically from `main`; Neon supplies Postgres and Upstash supplies Redis.

## API

- `GET /api/v1/repos/{owner}/{repo}/score`
- `GET /api/v1/repos/{owner}/{repo}/history`
- `POST /api/v1/repos/{owner}/{repo}/scan`
- `GET /badge/{owner}/{repo}.svg?metric=freshness`
- `GET /healthz`

## License

MIT
