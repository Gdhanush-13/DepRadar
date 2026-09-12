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
MAX_MANIFEST_DEPENDENCIES = 100
MAX_METADATA_RESPONSE_BYTES = 5_000_000


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
            if r.status_code != 200 or len(r.content) > MAX_METADATA_RESPONSE_BYTES:
                continue
            result.extend(parse_manifest(filename, r.text))
        tree = await client.get(
            f"https://api.github.com/repos/{owner}/{repo}/git/trees/{branch}?recursive=1",
            headers={"Accept": "application/vnd.github+json", **({"Authorization": f"Bearer {settings.github_token}"} if settings.github_token else {})},
        )
        if tree.status_code == 200 and len(tree.content) <= MAX_METADATA_RESPONSE_BYTES:
            for path in tree.json().get("tree", []):
                filename = path.get("path", "")
                if not filename.lower().endswith(".csproj"):
                    continue
                r = await client.get(
                    f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{filename}",
                    headers=headers,
                )
                if r.status_code == 200 and len(r.content) <= MAX_METADATA_RESPONSE_BYTES:
                    result.extend(parse_manifest(".csproj", r.text))
                    if len(result) >= MAX_MANIFEST_DEPENDENCIES:
                        break
    return result[:MAX_MANIFEST_DEPENDENCIES]


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


def _osv_version(required: str) -> str | None:
    if required == "vcs":
        return None
    match = re.search(r"(\d+(?:\.\d+)+(?:[-+][0-9A-Za-z.-]+)?)", required)
    return match.group(1) if match else None


def _vulnerability_severity(vulnerability: dict[str, Any]) -> str:
    database_severity = str(vulnerability.get("database_specific", {}).get("severity", "")).upper()
    if database_severity in {"CRITICAL", "HIGH", "MEDIUM", "LOW"}:
        return database_severity
    for item in vulnerability.get("severity", []):
        score = str(item.get("score", ""))
        if score.startswith("CVSS:3"):
            if all(metric in score for metric in ("C:H", "I:H", "A:H")):
                return "CRITICAL"
            return "HIGH"
        if score.startswith("AV:"):
            return "HIGH"
        match = re.search(r"(?:^|\s)(\d+(?:\.\d+)?)", score)
        if match:
            value = float(match.group(1))
            return "CRITICAL" if value >= 9 else "HIGH" if value >= 7 else "MEDIUM" if value >= 4 else "LOW"
    return "MEDIUM"


def parse_osv_vulnerabilities(data: dict[str, Any]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    ids: list[str] = []
    severities: list[str] = []
    seen: set[str] = set()
    for vulnerability in data.get("vulns", []):
        aliases = vulnerability.get("aliases", [])
        cve = next((alias for alias in aliases if alias.startswith("CVE-")), None)
        identifier = cve or vulnerability.get("id")
        if identifier and str(identifier) not in seen:
            seen.add(str(identifier))
            ids.append(str(identifier))
            severities.append(_vulnerability_severity(vulnerability))
    return tuple(ids), tuple(severities)


async def _osv_lookup(
    client: httpx.AsyncClient, ecosystem: str, name: str, required: str
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    version = _osv_version(required)
    if version is None:
        return (), ()
    response = await client.post(
        "https://api.osv.dev/v1/query",
        json={"package": {"ecosystem": ecosystem, "name": name}, "version": version},
    )
    if response.status_code != 200:
        return (), ()
    return parse_osv_vulnerabilities(response.json())


def _github_source(value: Any) -> tuple[str, str] | None:
    if isinstance(value, dict):
        value = value.get("url") or value.get("directory")
    if not isinstance(value, str):
        return None
    match = re.search(r"github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)", value)
    if not match:
        return None
    return match.group(1), match.group(2).removesuffix(".git")


async def _archive_status(client: httpx.AsyncClient, source: Any, headers: dict[str, str]) -> bool | None:
    github = _github_source(source)
    if not github:
        return None
    response = await client.get(f"https://api.github.com/repos/{github[0]}/{github[1]}", headers=headers)
    if response.status_code != 200:
        return None
    return bool(response.json().get("archived", False))


async def package_meta(
    eco: str, name: str, required: str = "unbounded"
) -> tuple[str, int, int, bool | None, tuple[str, ...], tuple[str, ...]]:
    if eco not in {"pypi", "npm", "nuget"}:
        raise ValueError(f"Unsupported ecosystem: {eco}")
    url = (
        f"https://pypi.org/pypi/{name}/json"
        if eco == "pypi" else f"https://registry.npmjs.org/{name}"
        if eco == "npm" else f"https://api.nuget.org/v3-flatcontainer/{name.lower()}/index.json"
    )
    async with httpx.AsyncClient(timeout=12) as client:
        response = await client.get(url)
        response.raise_for_status()
        if len(response.content) > MAX_METADATA_RESPONSE_BYTES:
            raise ValueError(f"Metadata response for {name} is too large")
        data = response.json()
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
            dates = [
                upload.get("upload_time_iso_8601", "")
                for releases in data.get("releases", {}).values()
                for upload in releases
            ]
            newest = max(dates, default="")
            info = data.get("info", {})
            sources = list((info.get("project_urls") or {}).values()) + [info.get("home_page")]
            source = next((item for item in sources if _github_source(item)), None)
            osv_ecosystem = "PyPI"
        elif eco == "npm":
            newest = data.get("time", {}).get(latest, "")
            source = data.get("repository")
            osv_ecosystem = "npm"
        else:
            registration = await client.get(
                f"https://api.nuget.org/v3/registration5-semver1/{name.lower()}/index.json"
            )
            registration.raise_for_status()
            if len(registration.content) > MAX_METADATA_RESPONSE_BYTES:
                raise ValueError(f"NuGet registration for {name} is too large")
            catalog = [
                item.get("catalogEntry", {})
                for page in registration.json().get("items", [])
                for item in page.get("items", [])
            ]
            newest = max((item.get("published", "") for item in catalog), default="")
            latest_catalog: dict[str, Any] = next((item for item in catalog if item.get("version") == latest), {})
            source = latest_catalog.get("repository") or latest_catalog.get("projectUrl")
            osv_ecosystem = "NuGet"
        github_headers = {"Accept": "application/vnd.github+json"}
        if settings.github_token:
            github_headers["Authorization"] = f"Bearer {settings.github_token}"
        archived = await _archive_status(client, source, github_headers)
        cve_ids, cve_severities = await _osv_lookup(client, osv_ecosystem, name, required)
    return (
        latest or "unknown",
        _versions_behind(required, versions),
        _iso_days_old(newest),
        archived,
        cve_ids,
        cve_severities,
    )
