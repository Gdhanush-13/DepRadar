from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.config import settings, validate_production_config
from app.db import SessionLocal, init_db
from app.main import app, risk_color, scan_rate_limiter, scan_repo
from app.models import Dependency, DependencySnapshot, Repository, Scan


def test_production_configuration_rejects_placeholder_secrets(monkeypatch):
    monkeypatch.setattr(settings, "depradar_env", "production")
    monkeypatch.setattr(settings, "jwt_secret", "dev-secret-change-me")
    monkeypatch.setattr(settings, "internal_rescan_key", "dev-internal-key-change-me")
    with pytest.raises(RuntimeError, match="JWT_SECRET.*INTERNAL_RESCAN_KEY"):
        validate_production_config()


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
async def test_scan_persists_unknown_archive_metadata_as_false(monkeypatch):
    async def fake_repo(owner, repo):
        return {"default_branch": "main", "pushed_at": "fixture"}

    async def fake_manifest(owner, repo, branch):
        return [("nuget", "fixture-package", "0.1")]

    async def fake_meta(eco, name, required="unbounded"):
        return "0.2", 0, 24, None, (), ()

    monkeypatch.setattr("app.main.github_repo", fake_repo)
    monkeypatch.setattr("app.main.manifest", fake_manifest)
    monkeypatch.setattr("app.main.package_meta", fake_meta)
    await init_db()
    async with SessionLocal() as db:
        scan = await scan_repo("archive-owner", "archive-repo", db)
        snapshot = (await db.execute(
            select(DependencySnapshot).where(DependencySnapshot.scan_id == scan.id)
        )).scalar_one()
    assert snapshot.is_archived_upstream is False


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
async def test_public_scan_rate_limit_returns_retry_after(monkeypatch):
    monkeypatch.setattr(settings, "scan_rate_limit_per_window", 1)
    monkeypatch.setattr(settings, "scan_rate_window_seconds", 60)
    await scan_rate_limiter.clear()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            first = await client.post("/scan", data={"repo": "not-a-repo"})
            second = await client.post("/scan", data={"repo": "not-a-repo"})
        assert first.status_code == 422
        assert second.status_code == 429
        assert "Too many scans from this location" in second.text
        assert 1 <= int(second.headers["retry-after"]) <= 60
        assert second.headers["x-ratelimit-remaining"] == "0"
    finally:
        await scan_rate_limiter.clear()


@pytest.mark.asyncio
async def test_scan_rate_limit_allows_requests_up_to_limit(monkeypatch):
    monkeypatch.setattr(settings, "scan_rate_limit_per_window", 2)
    monkeypatch.setattr(settings, "scan_rate_window_seconds", 60)
    await scan_rate_limiter.clear()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            responses = [await client.post("/scan", data={"repo": "not-a-repo"}) for _ in range(3)]
        assert [response.status_code for response in responses] == [422, 422, 429]
    finally:
        await scan_rate_limiter.clear()


@pytest.mark.asyncio
async def test_scan_rate_limit_is_per_forwarded_ip(monkeypatch):
    monkeypatch.setattr(settings, "scan_rate_limit_per_window", 1)
    monkeypatch.setattr(settings, "scan_rate_window_seconds", 60)
    await scan_rate_limiter.clear()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            first_ip = {"X-Forwarded-For": "203.0.113.10, 10.0.0.1"}
            second_ip = {"X-Forwarded-For": "203.0.113.11"}
            first = await client.post("/scan", data={"repo": "not-a-repo"}, headers=first_ip)
            blocked = await client.post("/scan", data={"repo": "not-a-repo"}, headers=first_ip)
            independent = await client.post("/scan", data={"repo": "not-a-repo"}, headers=second_ip)
        assert first.status_code == 422
        assert blocked.status_code == 429
        assert independent.status_code == 422
    finally:
        await scan_rate_limiter.clear()


@pytest.mark.asyncio
async def test_scan_rate_limit_expires_after_window(monkeypatch):
    monkeypatch.setattr(settings, "scan_rate_limit_per_window", 1)
    monkeypatch.setattr(settings, "scan_rate_window_seconds", 10)
    await scan_rate_limiter.clear()
    try:
        assert (await scan_rate_limiter.check("expiry-ip", now=100))[0] is True
        assert (await scan_rate_limiter.check("expiry-ip", now=109))[0] is False
        assert (await scan_rate_limiter.check("expiry-ip", now=110))[0] is True
    finally:
        await scan_rate_limiter.clear()


@pytest.mark.asyncio
async def test_api_scan_limit_returns_json_and_gets_are_unaffected(monkeypatch):
    monkeypatch.setattr(settings, "scan_rate_limit_per_window", 1)
    monkeypatch.setattr(settings, "scan_rate_window_seconds", 60)

    async def missing_repo(owner, repo):
        raise ValueError("GitHub repository was not found")

    monkeypatch.setattr("app.main.github_repo", missing_repo)
    await scan_rate_limiter.clear()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            first = await client.post("/api/v1/repos/foo/bar/scan")
            blocked = await client.post("/api/v1/repos/foo/bar/scan")
            badge = await client.get("/badge/example/example.svg")
        assert first.status_code == 422
        assert blocked.status_code == 429
        assert blocked.json()["detail"] == "Too many scans from this location — try again in a few minutes"
        assert badge.status_code == 200
    finally:
        await scan_rate_limiter.clear()

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
                                      is_archived_upstream=False, points_deducted=10,
                                      lag_points=8, age_points=2))
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
        repo = Repository(owner="ignore", name=suffix, full_name=f"ignore/{suffix}")
        db.add(repo)
        await db.flush()
        dep = Dependency(repository_id=repo.id, ecosystem="pypi", name="pytest", current_version_required="1")
        db.add(dep)
        await db.flush()
        scan = Scan(repository_id=repo.id, status="complete", freshness_score=50, risk_score=20)
        db.add(scan)
        await db.flush()
        db.add(DependencySnapshot(scan_id=scan.id, dependency_id=dep.id,
                                  is_archived_upstream=False, points_deducted=50))
        await db.commit()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        denied = await client.post(f"/api/v1/repos/ignore/{suffix}/dependencies/pytest/ignore?ignored=true")
        changed = await client.post(f"/api/v1/repos/ignore/{suffix}/dependencies/pytest/ignore?ignored=true",
                                    headers={"X-Internal-Key": "dev-internal-key-change-me"})
        score = await client.get(f"/api/v1/repos/ignore/{suffix}/score")
        badge = await client.get(f"/badge/ignore/{suffix}.svg")
    assert denied.status_code == 401
    assert changed.status_code == 200 and changed.json()["ignored"] is True
    assert score.json()["status"] == "no_dependencies"
    assert score.json()["freshness_score"] is None and score.json()["risk_score"] is None
    assert "no dependencies" in badge.text
