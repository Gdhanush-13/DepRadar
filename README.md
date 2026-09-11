# DepRadar

DepRadar is a Codecov-style dependency health dashboard for public GitHub repositories. It scans supported manifests, compares package release freshness, calculates freshness and risk scores, and exposes an embeddable SVG badge.

## Live deployment

- App: https://depradar-backend-dev.onrender.com
- Health: https://depradar-backend-dev.onrender.com/healthz
- Example badge: https://depradar-backend-dev.onrender.com/badge/Gdhanush-13/DepRadar.svg
- API documentation: https://depradar-backend-dev.onrender.com/docs

The example badge is intentionally shown as `not scanned` until a repository scan completes. DepRadar never presents an unscanned repository as a score of zero.

## Features

- Responsive web UI for submitting `owner/repo` or a GitHub URL.
- Freshness and risk scores with dependency-level details.
- SVG badges for README files, with `?metric=freshness` or `?metric=risk`.
- Scan history and a score trend page.
- JSON API for scores, history, scans, health, and scheduled rescans.
- SQLite for local development and PostgreSQL for production.

## Supported manifests

| Manifest | Ecosystem | Fields read |
| --- | --- | --- |
| `requirements.txt` | PyPI | pinned and unpinned requirements |
| `pyproject.toml` | PyPI | PEP 621 and Poetry dependencies |
| `package.json` | npm | `dependencies` and `devDependencies` |

Up to 100 dependencies are included in a scan. Package metadata is read from PyPI or npm; the current adapter reports release freshness and basic upstream status.

## Score semantics

Freshness is a 0-100 score where higher is better. Risk is a 0-100 pressure score where lower is better. Badge colors use green for 80+, yellow for 50-79, and red below 50. A repository without a completed scan receives a gray `not scanned` badge.

## Run locally

```bash
python -m venv .venv
# Windows PowerShell
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
uvicorn app.main:app --reload
```

The local default uses SQLite and an in-process Redis fallback. For the full container stack:

```bash
docker compose up --build
```

Open http://localhost:8000. Never commit `.env`; copy `.env.example` and keep production credentials in the hosting provider.

## Configuration

Required in production:

| Variable | Purpose |
| --- | --- |
| `DATABASE_URL` | PostgreSQL URL; use the `postgresql+asyncpg://` scheme |
| `REDIS_URL` | Redis connection URL, including `rediss://` when TLS is required |
| `JWT_SECRET` | Long random signing secret |
| `APP_BASE_URL` | Public application URL used in badge links |

Optional variables include `GITHUB_TOKEN` (recommended in production to avoid GitHub API rate limits) and `INTERNAL_RESCAN_KEY` for the protected rescan endpoint.

## API and routes

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/` | Web dashboard |
| `POST` | `/scan` | Form-based repository scan |
| `GET` | `/badge/{owner}/{repo}.svg` | Freshness badge |
| `GET` | `/badge/{owner}/{repo}.svg?metric=risk` | Risk badge |
| `GET` | `/history/{owner}/{repo}` | Web scan history |
| `GET` | `/api/v1/repos/{owner}/{repo}/score` | Latest score JSON |
| `GET` | `/api/v1/repos/{owner}/{repo}/history` | History JSON |
| `POST` | `/api/v1/repos/{owner}/{repo}/scan` | Programmatic scan |
| `GET` | `/healthz` | Database health check |

Embed a badge in another repository with:

```markdown
[![DepRadar](https://depradar-backend-dev.onrender.com/badge/OWNER/REPO.svg)](https://depradar-backend-dev.onrender.com/)
```

## Deployment

The production service is deployed on Render from the `main` branch using the repository Dockerfile. Neon provides PostgreSQL and Upstash provides Redis. Render health checks use `/healthz`, and pushes to `main` trigger a new deployment.

A public scan requires the target repository to be accessible to GitHub's API. Configure a GitHub token in Render for reliable production use; do not put tokens or database URLs in source control.

## Quality checks

```bash
python -m ruff check app tests
python -m mypy app
python -m pytest --cov=app --cov-report=term-missing
```

The current test suite covers web routes, badge states, the scan-to-badge flow, manifest parsers, scoring, and the protected internal endpoint.

## License

MIT
