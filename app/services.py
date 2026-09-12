import json
import re
import tomllib
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from typing import Any

import httpx
from packaging.version import InvalidVersion, Version

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
        tree = await client.get(
            f"https://api.github.com/repos/{owner}/{repo}/git/trees/{branch}?recursive=1",
            headers={"Accept": "application/vnd.github+json", **({"Authorization": f"Bearer {settings.github_token}"} if settings.github_token else {})},
        )
        if tree.status_code == 200:
            for path in tree.json().get("tree", []):
                filename = path.get("path", "")
                if not filename.lower().endswith(".csproj"):
                    continue
                r = await client.get(
                    f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{filename}",
                    headers=headers,
                )
                if r.status_code == 200:
                    result.extend(parse_manifest(".csproj", r.text))
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
    if filename == ".csproj":
        root = ET.fromstring(content)
        result = []
        for reference in root.iter():
            if reference.tag.rsplit("}", 1)[-1] != "PackageReference":
                continue
            name = reference.attrib.get("Include") or reference.attrib.get("Update")
            version = reference.attrib.get("Version")
            if not version:
                version_node = next((node for node in reference if node.tag.rsplit("}", 1)[-1] == "Version"), None)
                version = version_node.text.strip() if version_node is not None and version_node.text else "unbounded"
            if name:
                result.append(("nuget", name, version))
        return result
    result = []
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "--hash")):
            continue
        if line.startswith(("-e ", "--editable ", "git+")):
            egg = re.search(r"#egg=([A-Za-z0-9_.-]+)", line)
            if egg:
                result.append(("pypi", egg.group(1), "vcs"))
            continue
        if line.startswith("-"):
            continue
        name, version = _split_requirement(line)
        result.append(("pypi", name, version))
    return result


def _split_requirement(value: str) -> tuple[str, str]:
    # pip-compile uses a trailing backslash for continued hash lines.
    value = value.split("\\", 1)[0].split(";", 1)[0].strip()
    match = re.match(r"^([A-Za-z0-9_.-]+)\s*(.*)$", value)
    if not match:
        return value, "unbounded"
    return match.group(1), match.group(2) or "unbounded"


def _versions_behind(required: str, versions: list[str]) -> int:
    match = re.search(r"(\d+(?:\.\d+)+(?:[-+][0-9A-Za-z.-]+)?)", required)
    if not match:
        return 0
    try:
        current = Version(match.group(1))
        return min(100, sum(Version(candidate) > current for candidate in versions))
    except InvalidVersion:
        return 0


def _iso_days_old(value: str) -> int:
    if not value:
        return 0
    try:
        return max(0, (datetime.now(UTC) - datetime.fromisoformat(value)).days)
    except ValueError:
        return 0


async def package_meta(eco: str, name: str, required: str = "unbounded") -> tuple[str, int, int, bool, tuple[str, ...]]:
    url = (
        f"https://pypi.org/pypi/{name}/json"
        if eco == "pypi" else f"https://registry.npmjs.org/{name}"
        if eco == "npm" else f"https://api.nuget.org/v3-flatcontainer/{name.lower()}/index.json"
    )
    async with httpx.AsyncClient(timeout=12) as client:
        r = await client.get(url)
        r.raise_for_status()
        data = r.json()
    versions: list[str] = (
        list(data.get("releases", {}).keys())
        if eco == "pypi" else list(data.get("versions", {}).keys())
        if eco == "npm" else data.get("versions", [])
    )
    latest = (
        data.get("info", {}).get("version")
        if eco == "pypi" else data.get("dist-tags", {}).get("latest", "unknown")
        if eco == "npm" else (data.get("versions") or ["unknown"])[-1]
    )
    if eco == "pypi":
        dates = [upload.get("upload_time_iso_8601", "") for releases in data.get("releases", {}).values() for upload in releases]
        newest = max(dates, default="")
        archived = bool(data.get("info", {}).get("yanked", False))
    elif eco == "npm":
        newest = data.get("time", {}).get(latest, "")
        archived = False
    else:
        async with httpx.AsyncClient(timeout=12) as client:
            registration = await client.get(f"https://api.nuget.org/v3/registration5-semver1/{name.lower()}/index.json")
        registration.raise_for_status()
        catalog = [item.get("catalogEntry", {}) for page in registration.json().get("items", []) for item in page.get("items", [])]
        newest = max((item.get("published", "") for item in catalog), default="")
        archived = False
    return latest or "unknown", _versions_behind(required, versions), _iso_days_old(newest), archived, ()
