export const GITHUB_REPOSITORY_URL_ERRORS = {
  https: "请输入 HTTPS GitHub 仓库地址",
  host: "目前仅支持 github.com 仓库",
  credentials: "仓库地址不能包含用户名、密码或 Token",
  path: "请输入仓库主页地址，例如 https://github.com/owner/repo",
} as const;

export type GitHubRepositoryUrlErrorCode = keyof typeof GITHUB_REPOSITORY_URL_ERRORS;

export class GitHubRepositoryUrlError extends Error {
  readonly code: GitHubRepositoryUrlErrorCode;

  constructor(code: GitHubRepositoryUrlErrorCode, message: string) {
    super(message);
    this.name = "GitHubRepositoryUrlError";
    this.code = code;
  }
}

export function normalizeGitHubRepositoryUrl(value: string): string {
  const text = value.trim();
  if (!text) {
    throw new GitHubRepositoryUrlError("https", GITHUB_REPOSITORY_URL_ERRORS.https);
  }

  let parsed: URL;
  try {
    parsed = new URL(text);
  } catch {
    throw new GitHubRepositoryUrlError("https", GITHUB_REPOSITORY_URL_ERRORS.https);
  }

  if (parsed.protocol !== "https:") {
    throw new GitHubRepositoryUrlError("https", GITHUB_REPOSITORY_URL_ERRORS.https);
  }
  if (parsed.username || parsed.password) {
    throw new GitHubRepositoryUrlError("credentials", GITHUB_REPOSITORY_URL_ERRORS.credentials);
  }

  let host = parsed.hostname.replace(/\.$/, "").toLowerCase();
  if (host === "www.github.com") {
    host = "github.com";
  }
  if (host !== "github.com") {
    throw new GitHubRepositoryUrlError("host", GITHUB_REPOSITORY_URL_ERRORS.host);
  }
  if (parsed.search || parsed.hash) {
    throw new GitHubRepositoryUrlError("path", GITHUB_REPOSITORY_URL_ERRORS.path);
  }

  const segments = parsed.pathname.split("/").filter(Boolean);
  if (segments.length !== 2) {
    throw new GitHubRepositoryUrlError("path", GITHUB_REPOSITORY_URL_ERRORS.path);
  }

  const owner = segments[0];
  let repo = segments[1];
  if (repo.toLowerCase().endsWith(".git")) {
    repo = repo.slice(0, -4);
  }
  if (!owner || !repo) {
    throw new GitHubRepositoryUrlError("path", GITHUB_REPOSITORY_URL_ERRORS.path);
  }
  return `https://github.com/${owner}/${repo}`;
}
