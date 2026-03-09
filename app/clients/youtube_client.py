from __future__ import annotations

import html as html_lib
import re
from typing import Any

import httpx

from app.utils import parse_abbreviated_number


async def fetch_youtube_stats(identifier: str, timeout_seconds: int) -> dict[str, Any]:
    timeout = httpx.Timeout(timeout_seconds)
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept-Language": "en-US,en;q=0.9",
    }

    target = identifier.strip()
    if target.startswith("UC"):
        path = f"/channel/{target}/about"
    elif target.startswith("@"):
        path = f"/{target}/about"
    else:
        path = f"/@{target}/about"

    url = f"https://www.youtube.com{path}"

    async with httpx.AsyncClient(timeout=timeout, headers=headers, follow_redirects=True) as client:
        resp = await client.get(url, params={"hl": "en"})
        if _is_consent_page(resp):
            continue_url = await _accept_consent_and_get_continue_url(client, resp.text)
            if continue_url:
                resp = await client.get(continue_url)
        if resp.status_code == 404:
            raise ValueError(f"YouTube channel not found: {identifier}")
        resp.raise_for_status()
        html = resp.text

    if _looks_like_consent_html(html):
        raise ValueError(
            f"YouTube returned a consent page for {identifier}. Try again or use a different region/network."
        )

    subscriber_text = _extract_subscriber_text(html)
    if not subscriber_text:
        subscriber_text = _extract_subscriber_from_meta_description(html)
    if not subscriber_text:
        raise ValueError(
            f"YouTube subscriber count unavailable for {identifier}. The channel may hide subscribers."
        )

    clean_value = (
        subscriber_text.lower()
        .replace("subscribers", "")
        .replace("subscriber", "")
        .strip()
    )
    subscribers = parse_abbreviated_number(clean_value)

    title_match = re.search(r'<meta property="og:title" content="([^"]+)"', html)
    channel_name = title_match.group(1) if title_match else identifier

    return {
        "platform": "youtube",
        "identifier": identifier,
        "channel_name": channel_name,
        "subscribers_text": subscriber_text,
        "subscribers": subscribers,
        "channel_url": str(resp.url).split("?")[0],
        "source": "youtube_html_scrape",
    }


def _extract_subscriber_text(html: str) -> str | None:
    patterns = [
        r'"subscriberCountText":"([^"]+)"',
        r'"subscriberCountText":\{"simpleText":"([^"]+)"\}',
        r'"subscriberCountText":\{"runs":\[\{"text":"([^"]+)"\}\]\}',
        r'"label":"([0-9\.,A-Za-z\s]+subscribers)"',
    ]

    for pattern in patterns:
        match = re.search(pattern, html)
        if match:
            return match.group(1).strip()

    return None


def _extract_subscriber_from_meta_description(html: str) -> str | None:
    match = re.search(r'<meta\s+name="description"\s+content="([^"]+)"', html)
    if not match:
        return None

    description = match.group(1)
    label_match = re.search(
        r"([0-9][0-9\.,\s]*(?:[KMBkmb]|million|thousand|billion)?)\s+subscribers",
        description,
        flags=re.IGNORECASE,
    )
    if not label_match:
        return None
    return f"{label_match.group(1)} subscribers"


def _is_consent_page(response: httpx.Response) -> bool:
    return "consent.youtube.com" in str(response.url)


def _looks_like_consent_html(html: str) -> bool:
    return "consent.youtube.com/save" in html and "Accept all" in html


async def _accept_consent_and_get_continue_url(
    client: httpx.AsyncClient,
    consent_html: str,
) -> str | None:
    forms = re.findall(
        r'(<form[^>]*action="https://consent\.youtube\.com/save"[^>]*>.*?</form>)',
        consent_html,
        flags=re.DOTALL,
    )
    if not forms:
        return None

    form_html = next((form for form in forms if "Accept all" in form), forms[0])
    pairs = re.findall(r'name="([^"]+)"\s+value="([^"]*)"', form_html)
    if not pairs:
        return None

    data = {key: html_lib.unescape(value) for key, value in pairs}
    await client.post("https://consent.youtube.com/save", data=data)
    return data.get("continue")
