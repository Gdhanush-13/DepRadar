import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.db import SessionLocal, init_db
from app.main import app, scan_repo
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
async def test_badge_for_unscanned_repo_is_explicit():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/badge/never-scanned/repository.svg")
    assert response.status_code == 200
    assert "not scanned" in response.text and '>0<' not in response.text


@pytest.mark.asyncio
async def test_scan_then_badge_uses_computed_score(monkeypatch):
    async def fake_repo(owner, repo):
        return {"default_branch": "main", "pushed_at": "fixture"}

    async def fake_manifest(owner, repo, branch):
        return [("pypi", "fixture-package", "^1")]

    async def fake_meta(eco, name):
        return "2.0", 0, 0, False, ()

    monkeypatch.setattr("app.main.github_repo", fake_repo)
    monkeypatch.setattr("app.main.manifest", fake_manifest)
    monkeypatch.setattr("app.main.package_meta", fake_meta)
    await init_db()
    async with SessionLocal() as db:
        scan = await scan_repo("fixture-owner", "fixture-repo", db)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/badge/fixture-owner/fixture-repo.svg")
    assert scan.freshness_score == 100
    assert 'aria-label="DepRadar: 100"' in response.text


@pytest.mark.asyncio
async def test_home_history_and_scan_error_pages(monkeypatch):
    async def missing_repo(owner, repo):
        raise ValueError("GitHub repository was not found")

    monkeypatch.setattr("app.main.github_repo", missing_repo)
    async with SessionLocal() as db:
        repo = (await db.execute(select(Repository).where(Repository.full_name == "history/test"))).scalar_one_or_none()
        if repo is None:
            repo = Repository(owner="history", name="test", full_name="history/test")
            db.add(repo)
            await db.flush()
            db.add(Scan(repository_id=repo.id, status="complete", freshness_score=80, risk_score=20))
            await db.commit()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        home = await client.get("/")
        error = await client.post("/scan", data={"repo": "doesnotexist123/fake"})
        history = await client.get("/history/history/test")
    assert home.status_code == 200 and "Know when" in home.text
    assert error.status_code == 422 and "couldn’t complete" in error.text
    assert history.status_code == 200 and "Scan history" in history.text


@pytest.mark.asyncio
async def test_internal_rescan_requires_key():
    await init_db()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/internal/rescan-all", headers={"x-internal-key": "wrong"})
    assert response.status_code == 401

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
    assert "#ef4444" in badge.text
    assert missing.status_code == 404
