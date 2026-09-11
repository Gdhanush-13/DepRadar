import pytest

from app.services import normalize_repo, parse_manifest


def test_normalize_repo_accepts_url():
    assert normalize_repo("https://github.com/owner/repo/") == ("owner", "repo")

def test_normalize_repo_rejects_invalid():
    with pytest.raises(ValueError):
        normalize_repo("not-a-repo")


def test_parse_poetry_manifest():
    content = """[tool.poetry.dependencies]\npython = \"^3.11\"\nfastapi = \"^0.115\"\nhttpx = \"^0.27\"\n"""
    parsed = parse_manifest("pyproject.toml", content)
    assert [(name, version) for _, name, version in parsed] == [("fastapi", "^0.115"), ("httpx", "^0.27")]


def test_parse_pep621_manifest_and_repo_fixture():
    from pathlib import Path

    content = Path("pyproject.toml").read_text(encoding="utf-8")
    parsed = parse_manifest("pyproject.toml", content)
    assert len(parsed) >= 5 and all(ecosystem == "pypi" for ecosystem, _, _ in parsed)


def test_parse_requirements_fallback():
    assert parse_manifest("requirements.txt", "fastapi>=1\n# comment\nhttpx\n") == [
        ("pypi", "fastapi", ">=1"), ("pypi", "httpx", "unbounded")
    ]


def test_parse_package_json_dependencies():
    parsed = parse_manifest("package.json", '{"dependencies":{"alpinejs":"^3"},"devDependencies":{"vite":"^5"}}')
    assert parsed == [("npm", "alpinejs", "^3"), ("npm", "vite", "^5")]
