from datetime import UTC, datetime

from fastapi import Depends, FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .db import get_db, init_db
from .models import Dependency, DependencySnapshot, Repository, Scan
from .scoring import DependencySignals, dependency_score, freshness_score, risk_score
from .services import github_repo, manifest, normalize_repo, package_meta

app = FastAPI(title="DepRadar", version="1.0.0", description="Dependency freshness and risk radar")
templates = Jinja2Templates(directory="app/templates")


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
        return templates.TemplateResponse(
            request=request, name="results.html", context={"repo": f"{owner}/{name}", "scan": scan}
        )
    except Exception as exc:
        return templates.TemplateResponse(
            request=request, name="error.html", context={"message": str(exc)}, status_code=422
        )


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
        dep = Dependency(
            repository_id=repo.id, ecosystem=eco, name=dep_name, current_version_required=required
        )
        db.add(dep)
        await db.flush()
        latest_v, behind, days, archived, cves = await package_meta(eco, dep_name)
        sig = DependencySignals(behind, days, archived, cves)
        score = dependency_score(sig)
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
            )
        )
    scan.freshness_score = freshness_score(scores)
    scan.risk_score = risk_score(signals)
    scan.status = "complete"
    scan.finished_at = datetime.now(UTC)
    repo.last_scanned_at = scan.finished_at
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
        value = 0
    color = "#4c1" if value >= 80 else "#dfb317" if value >= 50 else "#e05d44"
    return Response(
        badge_svg("DepRadar", f"{value:.0f}", color),
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=300"},
    )
