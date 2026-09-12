FROM python:3.12-slim AS builder
WORKDIR /build
COPY pyproject.toml .
RUN pip install --no-cache-dir ".[production]"
FROM python:3.12-slim
WORKDIR /app
COPY --from=builder /usr/local /usr/local
COPY app app
COPY alembic alembic
COPY alembic.ini .
RUN useradd --create-home appuser
USER appuser
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
