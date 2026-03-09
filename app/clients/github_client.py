from __future__ import annotations

from typing import Any

import httpx


GITHUB_API_BASE = "https://api.github.com"


async def fetch_github_user_stats(username: str, timeout_seconds: int) -> dict[str, Any]:
    headers = {"User-Agent": "social-stats"}
    timeout = httpx.Timeout(timeout_seconds)

    async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
        user_resp = await client.get(f"{GITHUB_API_BASE}/users/{username}")
        if user_resp.status_code == 404:
            raise ValueError(f"GitHub user not found: {username}")
        user_resp.raise_for_status()
        user_data = user_resp.json()

        repos_resp = await client.get(
            f"{GITHUB_API_BASE}/users/{username}/repos",
            params={"sort": "updated", "per_page": 30},
        )
        repos_resp.raise_for_status()
        repos_data = repos_resp.json()

    repositories = [
        {
            "name": repo.get("name"),
            "full_name": repo.get("full_name"),
            "html_url": repo.get("html_url"),
            "stargazers_count": repo.get("stargazers_count"),
            "forks_count": repo.get("forks_count"),
            "updated_at": repo.get("updated_at"),
        }
        for repo in repos_data
    ]

    return {
        "platform": "github_user",
        "username": user_data.get("login", username),
        "name": user_data.get("name"),
        "followers": user_data.get("followers"),
        "following": user_data.get("following"),
        "public_repositories": user_data.get("public_repos"),
        "profile_url": user_data.get("html_url"),
        "repositories": repositories,
        "source": "github_public_api",
    }


async def fetch_github_repo_stats(owner: str, repo: str, timeout_seconds: int) -> dict[str, Any]:
    headers = {"User-Agent": "social-stats"}
    timeout = httpx.Timeout(timeout_seconds)

    async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
        repo_resp = await client.get(f"{GITHUB_API_BASE}/repos/{owner}/{repo}")
        if repo_resp.status_code == 404:
            raise ValueError(f"GitHub repo not found: {owner}/{repo}")
        repo_resp.raise_for_status()
        repo_data = repo_resp.json()

    return {
        "platform": "github_repo",
        "full_name": repo_data.get("full_name", f"{owner}/{repo}"),
        "description": repo_data.get("description"),
        "stars": repo_data.get("stargazers_count"),
        "forks": repo_data.get("forks_count"),
        "watchers": repo_data.get("watchers_count"),
        "open_issues": repo_data.get("open_issues_count"),
        "language": repo_data.get("language"),
        "default_branch": repo_data.get("default_branch"),
        "archived": repo_data.get("archived"),
        "license": (repo_data.get("license") or {}).get("spdx_id"),
        "topics": repo_data.get("topics", []),
        "updated_at": repo_data.get("updated_at"),
        "repo_url": repo_data.get("html_url"),
        "source": "github_public_api",
    }
