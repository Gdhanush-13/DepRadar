from datetime import UTC, datetime

from fastapi import Depends, FastAPI, Form, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .db import get_db, init_db
from .models import BadgeCache, Dependency, DependencySnapshot, Repository, Scan
from .scoring import (
    DependencySignals,
    dependency_deductions,
    dependency_score,
    freshness_score,
    risk_score,
)
from .services import github_repo, manifest, normalize_repo, package_meta

app = FastAPI(title="DepRadar", version="1.0.0", description="Dependency freshness and risk radar")
templates = Jinja2Templates(directory="app/templates")
app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.middleware("http")
async def inject_design_system(request: Request, call_next):
    response = await call_next(request)
    content_type = response.headers.get("content-type", "")
    if not content_type.startswith("text/html"):
        return response
    body = b"".join([chunk async for chunk in response.body_iterator])
    body = body.replace(b"</head>", b'<link rel="stylesheet" href="/static/styles.css"></head>', 1)
    headers = {key: value for key, value in response.headers.items() if key.lower() != "content-length"}
    return Response(body, status_code=response.status_code, headers=headers, media_type="text/html")


@app.on_event("startup")
async def startup() -> None:
    await init_db()


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(request=request, name="home.html", context={})


@app.post("/scan", response_class=HTMLResponse)
async def scan_form(request: Request, repo: str = Form(...), db: AsyncSession = Depends(get_db)):
    try:
        owner, name = normalize_repo(repo)
        scan = await scan_repo(owner, name, db)
        dependencies = await dependency_rows(scan.id, db)
        return templates.TemplateResponse(request=request, name="results.html", context={
            "repo": f"{owner}/{name}", "scan": scan, "dependencies": dependencies,
            "base_url": settings.app_base_url.rstrip("/"),
        })
    except Exception as exc:
        message = str(exc)
        if "rate limit" in message.lower():
            message = "GitHub's API rate limit was reached. Add GITHUB_TOKEN in Render and try again."
        return templates.TemplateResponse(
            request=request, name="error.html", context={"message": message, "repo": repo}, status_code=422
        )


async def dependency_rows(scan_id: int, db: AsyncSession) -> list[dict[str, object]]:
    rows = await db.execute(
        select(Dependency, DependencySnapshot)
        .join(DependencySnapshot, DependencySnapshot.dependency_id == Dependency.id)
        .where(DependencySnapshot.scan_id == scan_id)
    )
    return [{
        "name": dep.name, "ecosystem": dep.ecosystem, "required": dep.current_version_required,
        "latest": snap.latest_version, "behind": snap.versions_behind,
        "days": snap.days_since_last_release, "archived": snap.is_archived_upstream,
        "cves": snap.known_cves, "score": max(0, 100 - snap.points_deducted),
        "ignored": dep.is_ignored,
        "deductions": {"lag": snap.lag_points, "age": snap.age_points,
                        "archived": snap.archived_points, "cves": snap.cve_points},
    } for dep, snap in rows.all()]


async def latest(repo: Repository, db: AsyncSession):
    result = await db.execute(
        select(Scan).where(Scan.repository_id == repo.id).order_by(Scan.id.desc()).limit(1)
    )
    return result.scalar_one_or_none()


@app.get("/healthz")
async def healthz(db: AsyncSession = Depends(get_db)):
    await db.execute(text("SELECT 1"))
    return {"status": "ok", "database": "ok", "redis": "optional-local-fallback"}


async def scan_repo(owner: str, name: str, db: AsyncSession) -> Scan:
    info = await github_repo(owner, name)
    repo = (
        await db.execute(select(Repository).where(Repository.full_name == f"{owner}/{name}"))
    ).scalar_one_or_none()
    if not repo:
        repo = Repository(
            owner=owner,
            name=name,
            full_name=f"{owner}/{name}",
            default_branch=info.get("default_branch", "main"),
        )
        db.add(repo)
        await db.flush()
    scan = Scan(repository_id=repo.id, status="running", commit_sha_scanned=info.get("pushed_at"))
    db.add(scan)
    await db.flush()
    deps = await manifest(owner, name, repo.default_branch)
    if not deps:
        scan.status = "failed"
        await db.commit()
        raise ValueError("No supported manifest detected")
    signals = []
    scores = []
    for eco, dep_name, required in deps[:100]:
        dep = (await db.execute(select(Dependency).where(
            Dependency.repository_id == repo.id, Dependency.ecosystem == eco, Dependency.name == dep_name
        ).order_by(Dependency.id.desc()).limit(1))).scalar_one_or_none()
        if dep is None:
            dep = Dependency(repository_id=repo.id, ecosystem=eco, name=dep_name,
                             current_version_required=required)
            db.add(dep)
            await db.flush()
        else:
            dep.current_version_required = required
        latest_v, behind, days, archived, cves = await package_meta(eco, dep_name, required)
        sig = DependencySignals(behind, days, archived, cves)
        deductions = dependency_deductions(sig)
        score = dependency_score(sig)
        if not dep.is_ignored:
            signals.append(sig)
            scores.append(score)
        db.add(
            DependencySnapshot(
                scan_id=scan.id,
                dependency_id=dep.id,
                latest_version=latest_v,
                versions_behind=behind,
                days_since_last_release=days,
                is_archived_upstream=archived,
                known_cves=list(cves),
                points_deducted=100 - score,
                lag_points=deductions["lag"], age_points=deductions["age"],
                archived_points=deductions["archived"], cve_points=deductions["cves"],
            )
        )
    scan.freshness_score = freshness_score(scores)
    scan.risk_score = risk_score(signals)
    scan.status = "complete"
    scan.finished_at = datetime.now(UTC)
    repo.last_scanned_at = scan.finished_at
    cache = await db.get(BadgeCache, repo.full_name)
    body = badge_svg("DepRadar", f"{scan.freshness_score:.0f}", score_color(scan.freshness_score or 0))
    if cache is None:
        cache = BadgeCache(repo_full_name=repo.full_name, svg_body=body, score=scan.freshness_score or 0)
        db.add(cache)
    else:
        cache.svg_body, cache.score, cache.generated_at = body, scan.freshness_score or 0, datetime.now(UTC)
    await db.commit()
    return scan


@app.post("/api/v1/repos/{owner}/{repo}/scan", status_code=202)
async def trigger_scan(owner: str, repo: str, db: AsyncSession = Depends(get_db)):
    try:
        normalize_repo(f"{owner}/{repo}")
        scan = await scan_repo(owner, repo, db)
        return {"scan_id": scan.id, "status": scan.status}
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    except Exception as e:
        raise HTTPException(502, f"Scan failed: {e}") from e


@app.post("/api/v1/repos/{owner}/{repo}/dependencies/{dependency}/ignore")
async def set_dependency_ignored(
    owner: str, repo: str, dependency: str, ignored: bool = Query(...),
    x_user_id: int | None = Header(default=None), db: AsyncSession = Depends(get_db),
):
    item = (await db.execute(select(Repository).where(Repository.full_name == f"{owner}/{repo}"))).scalar_one_or_none()
    if not item or not item.registered_by_user_id or x_user_id != item.registered_by_user_id:
        raise HTTPException(401, "Only the user who registered this repository can change ignore state")
    dep = (await db.execute(select(Dependency).where(
        Dependency.repository_id == item.id, Dependency.name == dependency
    ))).scalar_one_or_none()
    scan = await latest(item, db)
    if not dep or not scan:
        raise HTTPException(404, "Dependency or completed scan not found")
    dep.is_ignored = ignored
    snapshots = (await db.execute(select(DependencySnapshot).where(DependencySnapshot.scan_id == scan.id))).scalars().all()
    active = []
    for snap in snapshots:
        snap_dep = await db.get(Dependency, snap.dependency_id)
        if snap_dep is not None and not snap_dep.is_ignored:
            active.append(max(0, 100 - snap.points_deducted))
    scan.freshness_score = freshness_score(active)
    cache = await db.get(BadgeCache, item.full_name)
    if cache:
        cache.score = scan.freshness_score
        cache.svg_body = badge_svg("DepRadar", f"{scan.freshness_score:.0f}", score_color(scan.freshness_score))
    await db.commit()
    return {"repository": item.full_name, "dependency": dependency, "ignored": ignored,
            "freshness_score": scan.freshness_score}


@app.post("/api/v1/internal/rescan-all")
async def rescan_all(
    x_internal_key: str | None = Header(default=None), db: AsyncSession = Depends(get_db)
):
    if x_internal_key != settings.internal_rescan_key:
        raise HTTPException(401, "Invalid internal key")
    repos = (await db.execute(select(Repository))).scalars().all()
    completed = 0
    for item in repos:
        try:
            await scan_repo(item.owner, item.name, db)
            completed += 1
        except Exception:  # noqa: S112
            continue
    return {"scanned": completed, "total": len(repos)}


@app.get("/api/v1/repos/{owner}/{repo}/score")
async def score(owner: str, repo: str, db: AsyncSession = Depends(get_db)):
    item = (
        await db.execute(select(Repository).where(Repository.full_name == f"{owner}/{repo}"))
    ).scalar_one_or_none()
    if not item:
        raise HTTPException(404, "Repository has not been scanned")
    scan = await latest(item, db)
    if not scan:
        raise HTTPException(404, "Repository has not been scanned")
    return {
        "repository": item.full_name,
        "status": scan.status,
        "freshness_score": scan.freshness_score,
        "risk_score": scan.risk_score,
        "scanned_at": scan.finished_at,
    }


@app.get("/api/v1/repos/{owner}/{repo}/history")
async def history(owner: str, repo: str, db: AsyncSession = Depends(get_db)):
    item = (
        await db.execute(select(Repository).where(Repository.full_name == f"{owner}/{repo}"))
    ).scalar_one_or_none()
    if not item:
        raise HTTPException(404, "Repository has not been scanned")
    rows = (
        (
            await db.execute(
                select(Scan).where(Scan.repository_id == item.id).order_by(Scan.started_at)
            )
        )
        .scalars()
        .all()
    )
    return {
        "repository": item.full_name,
        "history": [
            {
                "scanned_at": s.finished_at,
                "freshness_score": s.freshness_score,
                "risk_score": s.risk_score,
            }
            for s in rows
        ],
    }


def badge_svg(label: str, value: str, color: str) -> str:
    return f'<svg xmlns="http://www.w3.org/2000/svg" width="190" height="20" role="img" aria-label="{label}: {value}"><title>{label}: {value}</title><rect width="190" height="20" rx="3" fill="#555"/><rect x="100" width="90" height="20" rx="3" fill="{color}"/><text x="50" y="14" fill="#fff" text-anchor="middle" font-family="Verdana" font-size="11">{label}</text><text x="145" y="14" fill="#fff" text-anchor="middle" font-family="Verdana" font-size="11">{value}</text></svg>'


def score_color(value: float) -> str:
    return "#22c55e" if value >= 80 else "#eab308" if value >= 50 else "#ef4444"


@app.get("/badge/{owner}/{repo}.svg")
async def badge(
    owner: str, repo: str, metric: str = Query("freshness"), db: AsyncSession = Depends(get_db)
):
    item = (
        await db.execute(select(Repository).where(Repository.full_name == f"{owner}/{repo}"))
    ).scalar_one_or_none()
    scan = await latest(item, db) if item else None
    value = (scan.risk_score if metric == "risk" else scan.freshness_score) if scan else None
    if value is None:
        return Response(
            badge_svg("DepRadar", "not scanned", "#64748b"), media_type="image/svg+xml",
            headers={"Cache-Control": "public, max-age=60"},
        )
    color = score_color(value)
    return Response(
        badge_svg("DepRadar", f"{value:.0f}", color),
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=300"},
    )


@app.get("/history/{owner}/{repo}", response_class=HTMLResponse)
async def history_page(owner: str, repo: str, request: Request, db: AsyncSession = Depends(get_db)):
    item = (await db.execute(select(Repository).where(Repository.full_name == f"{owner}/{repo}"))).scalar_one_or_none()
    if not item:
        return templates.TemplateResponse(request=request, name="error.html", context={
            "message": "This repository has not been scanned yet.", "repo": f"{owner}/{repo}"
        }, status_code=404)
    scans = (await db.execute(select(Scan).where(Scan.repository_id == item.id).order_by(Scan.started_at))).scalars().all()
    return templates.TemplateResponse(request=request, name="history.html", context={
        "repo": item.full_name, "scans": scans
    })


@app.get("/methodology", response_class=HTMLResponse)
async def methodology(request: Request):
    return templates.TemplateResponse(request=request, name="methodology.html", context={})


async def scan_payload(repo: str, scan: Scan, dependencies: list[dict[str, object]]) -> dict[str, object]:
    return {"repository": repo, "status": scan.status, "freshness_score": scan.freshness_score,
            "risk_score": scan.risk_score, "scanned_at": scan.finished_at,
            "commit_sha_scanned": scan.commit_sha_scanned, "dependencies": dependencies}


@app.get("/api/v1/repos/{owner}/{repo}/export.json")
async def export_json(owner: str, repo: str, db: AsyncSession = Depends(get_db)):
    item = (await db.execute(select(Repository).where(Repository.full_name == f"{owner}/{repo}"))).scalar_one_or_none()
    scan = await latest(item, db) if item else None
    if not item or not scan:
        raise HTTPException(404, "Repository has not been scanned")
    return await scan_payload(item.full_name, scan, await dependency_rows(scan.id, db))


@app.get("/api/v1/repos/{owner}/{repo}/export.md", response_class=Response)
async def export_markdown(owner: str, repo: str, db: AsyncSession = Depends(get_db)):
    item = (await db.execute(select(Repository).where(Repository.full_name == f"{owner}/{repo}"))).scalar_one_or_none()
    scan = await latest(item, db) if item else None
    if not item or not scan:
        raise HTTPException(404, "Repository has not been scanned")
    deps = await dependency_rows(scan.id, db)
    lines = [f"# DepRadar report: {item.full_name}", "", f"- Freshness: {scan.freshness_score:.2f}",
             f"- Risk: {scan.risk_score:.2f}", f"- Scanned: {scan.finished_at}",
             f"- Commit: `{scan.commit_sha_scanned or 'unknown'}`", "", "| Dependency | Required | Latest | Score | State |", "| --- | --- | --- | ---: | --- |"]
    lines += [f"| {d['name']} | {d['required']} | {d['latest']} | {d['score']:.0f} | {'ignored' if d['ignored'] else 'counted'} |" for d in deps]
    return Response("\n".join(lines) + "\n", media_type="text/markdown",
                    headers={"Content-Disposition": f'attachment; filename="{owner}-{repo}-depradar.md"'})


@app.get("/compare", response_class=HTMLResponse)
async def compare_page(
    request: Request,
    a: str | None = Query(default=None),
    b: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    if not a or not b:
        return templates.TemplateResponse(request=request, name="compare_form.html")
    try:
        a_owner, a_name = normalize_repo(a); b_owner, b_name = normalize_repo(b)
        items = []
        for owner, name in ((a_owner, a_name), (b_owner, b_name)):
            item = (await db.execute(select(Repository).where(Repository.full_name == f"{owner}/{name}"))).scalar_one_or_none()
            scan = await latest(item, db) if item else None
            if not item or not scan or scan.status != "complete":
                scan = await scan_repo(owner, name, db)
                item = (await db.execute(select(Repository).where(
                    Repository.full_name == f"{owner}/{name}"
                ))).scalar_one()
            items.append((item, scan, await dependency_rows(scan.id, db)))
        merged: dict[str, list[dict[str, object]]] = {}
        for index, (_, _, deps) in enumerate(items):
            for dep in deps:
                merged.setdefault(str(dep["name"]), [{}, {}])[index] = dep
        return templates.TemplateResponse(request=request, name="compare.html", context={"a": items[0], "b": items[1], "merged": merged})
    except Exception as exc:
        return templates.TemplateResponse(request=request, name="error.html", context={"message": str(exc), "repo": f"{a} vs {b}"}, status_code=404)
