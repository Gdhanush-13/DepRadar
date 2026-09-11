import json
import re
import tomllib
from datetime import UTC, datetime
from typing import Any

import httpx

from .config import settings

DependencySpec = tuple[str, str, str]


def normalize_repo(value: str) -> tuple[str, str]:
    value = (
        value.strip()
        .removeprefix("https://github.com/")
        .removeprefix("http://github.com/")
        .strip("/")
    )
    parts = value.split("/")
    if len(parts) != 2 or not all(re.fullmatch(r"[A-Za-z0-9_.-]+", p) for p in parts):
        raise ValueError("Enter a GitHub repository as owner/repo")
    return parts[0], parts[1]


async def github_repo(owner: str, repo: str) -> dict[str, Any]:
    headers = {"Accept": "application/vnd.github+json"}
    if settings.github_token:
        headers["Authorization"] = f"Bearer {settings.github_token}"
    async with httpx.AsyncClient(timeout=12) as client:
        response = await client.get(f"https://api.github.com/repos/{owner}/{repo}", headers=headers)
        if response.status_code == 404:
            raise ValueError("GitHub repository was not found")
        response.raise_for_status()
        return response.json()


async def manifest(owner: str, repo: str, branch: str) -> list[tuple[str, str, str]]:
    headers = {"Accept": "application/vnd.github.raw+json"}
    if settings.github_token:
        headers["Authorization"] = f"Bearer {settings.github_token}"
    result: list[DependencySpec] = []
    async with httpx.AsyncClient(timeout=12) as client:
        for filename, eco in (
            ("requirements.txt", "pypi"),
            ("pyproject.toml", "pypi"),
            ("package.json", "npm"),
        ):
            r = await client.get(
                f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{filename}",
                headers=headers,
            )
            if r.status_code != 200:
                continue
            result.extend(parse_manifest(filename, r.text))
    return result


def parse_manifest(filename: str, content: str) -> list[DependencySpec]:
    """Parse common Python and JavaScript dependency manifests."""
    if filename == "package.json":
        data = json.loads(content)
        return [("npm", name, version) for name, version in {
            **data.get("dependencies", {}), **data.get("devDependencies", {})
        }.items()]
    if filename == "pyproject.toml":
        data = tomllib.loads(content)
        raw = data.get("project", {}).get("dependencies", [])
        raw += list(data.get("tool", {}).get("poetry", {}).get("dependencies", {}).items())
        result = []
        for item in raw:
            name, version = item if isinstance(item, tuple) else _split_requirement(item)
            if name.lower() != "python":
                result.append(("pypi", name, version))
        return result
    result = []
    for line in content.splitlines():
        line = line.strip()
        if line and not line.startswith(("#", "-")):
            name, version = _split_requirement(line)
            result.append(("pypi", name, version))
    return result


def _split_requirement(value: str) -> tuple[str, str]:
    match = re.match(r"^([A-Za-z0-9_.-]+)\s*(.*)$", value)
    if not match:
        return value, "unbounded"
    return match.group(1), match.group(2) or "unbounded"


async def package_meta(eco: str, name: str) -> tuple[str, int, int, bool, tuple[str, ...]]:
    url = (
        f"https://pypi.org/pypi/{name}/json"
        if eco == "pypi"
        else f"https://registry.npmjs.org/{name}"
    )
    async with httpx.AsyncClient(timeout=12) as client:
        r = await client.get(url)
        r.raise_for_status()
        data = r.json()
    latest = (
        data.get("info", {}).get("version")
        if eco == "pypi"
        else data.get("dist-tags", {}).get("latest", "unknown")
    )
    release = data.get("info", {}).get("release_urls", {}) if eco == "pypi" else {}
    dates = (
        [x.get("upload_time_iso_8601", "") for values in release.values() for x in values]
        if release
        else []
    )
    days = 0
    if dates:
        newest = max(dates).replace("Z", "+00:00")
        days = max(0, (datetime.now(UTC) - datetime.fromisoformat(newest)).days)
    return latest or "unknown", 0, days, bool(data.get("info", {}).get("yanked", False)), ()
