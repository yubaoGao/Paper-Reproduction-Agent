"""GitHub repository URL normalization at the intake/application boundary."""

from __future__ import annotations

from urllib.parse import urlsplit


class GitHubRepositoryUrlError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


HTTPS_MESSAGE = "请输入 HTTPS GitHub 仓库地址"
HOST_MESSAGE = "目前仅支持 github.com 仓库"
CREDENTIALS_MESSAGE = "仓库地址不能包含用户名、密码或 Token"
PATH_MESSAGE = "请输入仓库主页地址，例如 https://github.com/owner/repo"


def normalize_github_repository_url(value: str) -> str:
    """Return a canonical https://github.com/owner/repo URL."""
    text = value.strip()
    if not text:
        raise GitHubRepositoryUrlError("https", HTTPS_MESSAGE)
    parsed = urlsplit(text)
    if parsed.scheme.lower() != "https":
        raise GitHubRepositoryUrlError("https", HTTPS_MESSAGE)
    if parsed.username or parsed.password:
        raise GitHubRepositoryUrlError("credentials", CREDENTIALS_MESSAGE)
    host = (parsed.hostname or "").rstrip(".").lower()
    if host == "www.github.com":
        host = "github.com"
    if host != "github.com":
        raise GitHubRepositoryUrlError("host", HOST_MESSAGE)
    if parsed.query or parsed.fragment:
        raise GitHubRepositoryUrlError("path", PATH_MESSAGE)
    segments = [item for item in parsed.path.split("/") if item]
    if len(segments) != 2:
        raise GitHubRepositoryUrlError("path", PATH_MESSAGE)
    owner, repo = segments
    if repo.lower().endswith(".git"):
        repo = repo[:-4]
    if not owner or not repo:
        raise GitHubRepositoryUrlError("path", PATH_MESSAGE)
    return f"https://github.com/{owner}/{repo}"
