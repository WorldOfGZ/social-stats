from __future__ import annotations

from typing import Any

import httpx


DOCKER_HUB_API_BASE = "https://hub.docker.com/v2"


async def fetch_dockerhub_image_stats(
    namespace: str,
    image: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    timeout = httpx.Timeout(timeout_seconds)
    headers = {"User-Agent": "social-stats"}
    url = f"{DOCKER_HUB_API_BASE}/repositories/{namespace}/{image}/"

    async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
        resp = await client.get(url)
        if resp.status_code == 404:
            raise ValueError(f"Docker Hub image not found: {namespace}/{image}")
        resp.raise_for_status()
        payload = resp.json()

    return {
        "platform": "dockerhub",
        "namespace": payload.get("namespace") or namespace,
        "image": payload.get("name") or image,
        "full_name": payload.get("repo_name") or f"{namespace}/{image}",
        "description": payload.get("description"),
        "pull_count": payload.get("pull_count"),
        "star_count": payload.get("star_count"),
        "status": payload.get("status"),
        "is_private": payload.get("is_private"),
        "is_automated": payload.get("is_automated"),
        "last_updated": payload.get("last_updated"),
        "repo_url": f"https://hub.docker.com/r/{namespace}/{image}",
        "source": "dockerhub_public_api",
    }
