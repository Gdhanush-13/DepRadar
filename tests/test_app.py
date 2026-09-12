from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.db import SessionLocal, init_db
from app.main import app, risk_color, scan_repo
from app.models import Dependency, DependencySnapshot, Repository, Scan, User


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

    async def fake_meta(eco, name, required="unbounded"):
        return "2.0", 0, 0, False, (), ()

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
    assert "#22c55e" in badge.text
    assert missing.status_code == 404


def test_risk_color_is_lower_is_better():
    assert risk_color(10) == "#22c55e"
    assert risk_color(40) == "#eab308"
    assert risk_color(80) == "#ef4444"


@pytest.mark.asyncio
async def test_methodology_exports_and_comparison():
    await init_db()
    async with SessionLocal() as db:
        repos = []
        for owner, name, required in (("export", "one", "1"), ("export", "two", "2")):
            repo = (await db.execute(select(Repository).where(Repository.full_name == f"{owner}/{name}"))).scalar_one_or_none()
            if repo is None:
                repo = Repository(owner=owner, name=name, full_name=f"{owner}/{name}")
                db.add(repo)
                await db.flush()
            dep = Dependency(repository_id=repo.id, ecosystem="pypi", name="shared", current_version_required=required)
            db.add(dep)
            await db.flush()
            scan = Scan(repository_id=repo.id, status="complete", freshness_score=80, risk_score=20,
                        commit_sha_scanned="abc123")
            db.add(scan)
            await db.flush()
            db.add(DependencySnapshot(scan_id=scan.id, dependency_id=dep.id, latest_version="3",
                                      points_deducted=10, lag_points=8, age_points=2))
            repos.append(repo)
        await db.commit()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        methodology = await client.get("/methodology")
        markdown = await client.get("/api/v1/repos/export/one/export.md")
        payload = await client.get("/api/v1/repos/export/one/export.json")
        comparison = await client.get("/compare?a=export/one&b=export/two")
    assert methodology.status_code == 200 and "Version lag" in methodology.text
    assert markdown.status_code == 200 and "# DepRadar report" in markdown.text and "| shared |" in markdown.text
    assert payload.status_code == 200 and payload.json()["commit_sha_scanned"] == "abc123"
    assert comparison.status_code == 200 and "shared" in comparison.text


@pytest.mark.asyncio
async def test_registered_dependency_ignore_recalculates_score():
    await init_db()
    suffix = uuid4().hex[:8]
    async with SessionLocal() as db:
        user = User(github_id=f"ignore-user-{suffix}", username=f"ignore-user-{suffix}")
        db.add(user)
        await db.flush()
        repo = Repository(owner="ignore", name=suffix, full_name=f"ignore/{suffix}", registered_by_user_id=user.id)
        db.add(repo)
        await db.flush()
        dep = Dependency(repository_id=repo.id, ecosystem="pypi", name="pytest", current_version_required="1")
        db.add(dep)
        await db.flush()
        scan = Scan(repository_id=repo.id, status="complete", freshness_score=50, risk_score=20)
        db.add(scan)
        await db.flush()
        db.add(DependencySnapshot(scan_id=scan.id, dependency_id=dep.id, points_deducted=50))
        await db.commit()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        denied = await client.post(f"/api/v1/repos/ignore/{suffix}/dependencies/pytest/ignore?ignored=true")
        changed = await client.post(f"/api/v1/repos/ignore/{suffix}/dependencies/pytest/ignore?ignored=true",
                                    headers={"X-User-ID": str(user.id)})
        score = await client.get(f"/api/v1/repos/ignore/{suffix}/score")
        badge = await client.get(f"/badge/ignore/{suffix}.svg")
    assert denied.status_code == 401
    assert changed.status_code == 200 and changed.json()["ignored"] is True
    assert score.json()["freshness_score"] == 100 and score.json()["risk_score"] == 0
    assert 'aria-label="DepRadar: 100"' in badge.text
