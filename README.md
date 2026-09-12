# DepRadar

DepRadar is a Codecov-style dependency health dashboard for public GitHub repositories. It scans supported manifests, measures dependency freshness and upstream risk, and publishes an embeddable SVG badge.

## Live deployment

- Dashboard: <https://depradar-backend-dev.onrender.com>
- Health check: <https://depradar-backend-dev.onrender.com/healthz>
- API docs: <https://depradar-backend-dev.onrender.com/docs>
- Example badge: <https://depradar-backend-dev.onrender.com/badge/Gdhanush-13/DepRadar.svg>

## What it does

- Accepts `owner/repo` values and full GitHub repository URLs.
- Reads Python, npm, and .NET dependency manifests.
- Reports a 0–100 freshness score and a 0–100 risk score.
- Shows package-level release age, version lag, archive status, OSV.dev CVE identifiers, severities, and scoring deductions.
- Provides history, Markdown/JSON exports, comparison, and README badges.
- Keeps unscanned repositories explicitly gray as `not scanned`.

## Supported manifests

| File | Ecosystem | Dependencies read |
| --- | --- | --- |
| `requirements.txt` | PyPI | Requirement lines, pinned or unpinned |
| `pyproject.toml` | PyPI | PEP 621 and Poetry dependencies |
| `package.json` | npm | `dependencies` and `devDependencies` |
| `*.csproj` | NuGet | XML `PackageReference` items, including nested project files; latest metadata comes from NuGet registration |

Scans include at most 100 dependencies. Package metadata is fetched from PyPI, npm, and NuGet; vulnerability records are queried from OSV.dev using exact package versions for PyPI and npm. A repository without a supported manifest returns a clear error; it is never reported as a successful empty scan.

## Scores

Freshness is higher-is-better. Risk is lower-is-better. Dependency deductions can come from version lag, release age, archived upstream projects, and OSV vulnerability severity. Multiple vulnerabilities accumulate deductions up to the per-dependency cap. Records without structured severity default to medium. The home-page scan progress indicator is only a loading hint and stops below 100% until the server returns results; the result page contains the authoritative score.

## Local development

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
uvicorn app.main:app --reload
```

Open <http://localhost:8000>. For a PostgreSQL development stack:

```bash
docker compose up --build
```

Copy `.env.example` to `.env`. Never commit `.env` or production credentials.

## Configuration

| Variable | Required in production | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | Yes | `postgresql+asyncpg://...` for PostgreSQL; SQLite is convenient locally |
| `JWT_SECRET` | Yes | Long random secret for future authenticated flows |
| `DEPRADAR_ENV` | Yes on Render | Set to `production` to reject placeholder secrets |
| `APP_BASE_URL` | Yes | Public URL used in badge links |
| `GITHUB_TOKEN` | Recommended | Raises GitHub API rate limits; read-only public access is sufficient |
| `INTERNAL_RESCAN_KEY` | Yes for nightly rescan | Secret sent by the scheduled workflow |
| `SCAN_RATE_LIMIT_PER_WINDOW` | Recommended | Anonymous scan attempts per client IP; defaults to 10 |
| `SCAN_RATE_WINDOW_SECONDS` | Recommended | Rolling rate-limit window; defaults to 600 seconds (10 minutes) |

## Routes and API

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/` | Dashboard and scan form |
| `POST` | `/scan` | Browser scan |
| `GET` | `/badge/{owner}/{repo}.svg` | Freshness badge (`?metric=risk` for risk) |
| `GET` | `/history/{owner}/{repo}` | History page |
| `GET` | `/methodology` | Scoring explanation |
| `GET` | `/compare?a=OWNER/REPO&b=OWNER/REPO` | Side-by-side comparison; missing scans are started automatically |
| `POST` | `/api/v1/repos/{owner}/{repo}/scan` | API scan |
| `GET` | `/api/v1/repos/{owner}/{repo}/score` | Latest scores |
| `GET` | `/api/v1/repos/{owner}/{repo}/history` | Score history JSON |
| `GET` | `/api/v1/repos/{owner}/{repo}/export.json` | Structured report |
| `GET` | `/api/v1/repos/{owner}/{repo}/export.md` | Markdown report |
| `POST` | `/api/v1/internal/rescan-all` | Protected nightly rescan endpoint |
| `GET` | `/healthz` | Database health check |

Badge example:

```markdown
[![DepRadar](https://depradar-backend-dev.onrender.com/badge/OWNER/REPO.svg)](https://depradar-backend-dev.onrender.com/)
```

## Deployment

The live service is deployed from `main` on Render using the repository `Dockerfile`. Neon PostgreSQL provides production data. Redis is not required by the current application and is intentionally not part of the deployment. Set the variables above in Render; do not store their values in GitHub. Every push to `main` runs CI and triggers the Render deployment integration.

The nightly GitHub Actions workflow calls `/api/v1/internal/rescan-all` with `APP_BASE_URL` and `INTERNAL_RESCAN_KEY` repository secrets. The deployed service does not include a separate worker process; this keeps scheduled work in the already configured workflow.

Public browser and API scans are limited to 10 attempts per client IP per 10-minute rolling window by default. A `429` response includes `Retry-After`. The single Render instance also caps active scans at two and limits each scan to 100 dependencies to keep memory bounded. The limiter is intentionally in-process; use a shared central store before running multiple app instances.

## Quality checks

```bash
python -m ruff check app tests
python -m mypy app
python -m pytest --cov=app --cov-report=term-missing
```

## Contributing

Keep changes focused, add tests for behavior changes, and run all three quality checks before opening a pull request. See [CONTRIBUTING.md](CONTRIBUTING.md).

## Roadmap

OAuth registration UI, SMTP alerts, organization dashboards, public galleries, Slack/webhook notifications, and Cargo/Go adapters remain intentionally unimplemented. No partial code paths for those features are shipped.

## License

MIT
