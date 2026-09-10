import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.db import SessionLocal, init_db
from app.main import app
from app.models import Repository, Scan


@pytest.mark.asyncio
async def test_health():
    await init_db()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/healthz")
    assert response.status_code == 200 and response.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_badge():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/badge/example/example.svg")
    assert response.status_code == 200 and response.text.startswith("<svg")

@pytest.mark.asyncio
async def test_score_history_and_cached_badges():
    await init_db()
    async with SessionLocal() as db:
        repo = (await db.execute(select(Repository).where(Repository.full_name == "acme/widget"))).scalar_one_or_none()
        if repo is None:
            repo = Repository(owner="acme", name="widget", full_name="acme/widget")
            db.add(repo)
            await db.flush()
        db.add(Scan(repository_id=repo.id, status="complete", freshness_score=88, risk_score=12))
        await db.commit()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        score = await client.get("/api/v1/repos/acme/widget/score")
        history = await client.get("/api/v1/repos/acme/widget/history")
        badge = await client.get("/badge/acme/widget.svg?metric=risk")
        missing = await client.get("/api/v1/repos/nope/nope/score")
    assert score.json()["freshness_score"] == 88
    assert len(history.json()["history"]) >= 1
    assert "#e05d44" in badge.text
    assert missing.status_code == 404
