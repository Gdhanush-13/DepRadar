# Contributing

## Development flow

1. Create a focused branch from `main`.
2. Copy `.env.example` to `.env` for local settings.
3. Add or update tests for behavior changes.
4. Run the complete local checks:

```bash
python -m ruff check app tests
python -m mypy app
python -m pytest --cov=app --cov-report=term-missing
```

5. Open a pull request with a short description of the user-visible change.

Do not commit `.env`, database files, generated `*.egg-info` output, credentials, or provider connection strings. The public scan path only supports repositories accessible through the GitHub API.
