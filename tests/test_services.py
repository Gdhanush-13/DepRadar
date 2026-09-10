import pytest

from app.services import normalize_repo


def test_normalize_repo_accepts_url():
    assert normalize_repo("https://github.com/owner/repo/") == ("owner", "repo")

def test_normalize_repo_rejects_invalid():
    with pytest.raises(ValueError):
        normalize_repo("not-a-repo")
