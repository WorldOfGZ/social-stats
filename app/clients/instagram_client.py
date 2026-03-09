from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote_plus

import httpx

from app.utils import parse_abbreviated_number


INSTAGRAM_WEB_PROFILE_ENDPOINT = "https://i.instagram.com/api/v1/users/web_profile_info/"


async def fetch_instagram_stats(username: str, timeout_seconds: int) -> dict[str, Any]:
    timeout = httpx.Timeout(timeout_seconds)
    headers = {
        "User-Agent": "Mozilla/5.0",
        "X-IG-App-ID": "936619743392459",
        "Accept": "application/json",
    }

    async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
        resp = await client.get(INSTAGRAM_WEB_PROFILE_ENDPOINT, params={"username": username})
        if resp.status_code == 200:
            payload = resp.json()
            user = (payload.get("data") or {}).get("user") or {}
            if user:
                return {
                    "platform": "instagram",
                    "username": user.get("username", username),
                    "private": user.get("is_private"),
                    "followers": ((user.get("edge_followed_by") or {}).get("count")),
                    "following": ((user.get("edge_follow") or {}).get("count")),
                    "followed": ((user.get("edge_follow") or {}).get("count")),
                    "posts": ((user.get("edge_owner_to_timeline_media") or {}).get("count")),
                    "profile_url": f"https://www.instagram.com/{user.get('username', username)}/",
                    "source": "instagram_web_profile_info",
                }

        page_resp = await client.get(f"https://www.instagram.com/{quote_plus(username)}/")
        if page_resp.status_code == 404:
            raise ValueError(f"Instagram user not found: {username}")
        page_resp.raise_for_status()
        html = page_resp.text

    followers = _extract_count(html, "edge_followed_by")
    following = _extract_count(html, "edge_follow")
    posts = _extract_count(html, "edge_owner_to_timeline_media")
    private = _extract_private(html)

    if followers is None or following is None or posts is None:
        og_followers, og_following, og_posts = _extract_from_og_description(html)
        if followers is None:
            followers = og_followers
        if following is None:
            following = og_following
        if posts is None:
            posts = og_posts

    if followers is None and following is None and posts is None:
        raise ValueError(
            f"Instagram data is unavailable for {username}. It may be private or blocked."
        )

    return {
        "platform": "instagram",
        "username": username,
        "private": private,
        "followers": followers,
        "following": following,
        "followed": following,
        "posts": posts,
        "profile_url": f"https://www.instagram.com/{username}/",
        "source": "instagram_html_scrape",
    }


def _extract_count(html: str, key: str) -> int | None:
    match = re.search(rf'"{re.escape(key)}":\{{"count":(\d+)', html)
    if not match:
        return None
    return int(match.group(1))


def _extract_private(html: str) -> bool | None:
    match = re.search(r'"is_private":(true|false)', html)
    if not match:
        return None
    return match.group(1) == "true"


def _extract_from_og_description(html: str) -> tuple[int | None, int | None, int | None]:
    match = re.search(r'<meta\s+property="og:description"\s+content="([^"]+)"', html)
    if not match:
        return (None, None, None)

    text = match.group(1)
    followers = _extract_labeled_value(text, "Followers")
    following = _extract_labeled_value(text, "Following")
    posts = _extract_labeled_value(text, "Posts")
    return (followers, following, posts)


def _extract_labeled_value(text: str, label: str) -> int | None:
    match = re.search(rf"([0-9][0-9\.,KMBkmb\s+]*)\s+{label}", text)
    if not match:
        return None
    return parse_abbreviated_number(match.group(1))
