from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Awaitable, Callable, Sequence

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

from app.cache import InMemoryCache
from app.clients.dockerhub_client import fetch_dockerhub_image_stats
from app.clients.github_client import fetch_github_repo_stats, fetch_github_user_stats
from app.clients.instagram_client import fetch_instagram_stats
from app.clients.youtube_client import fetch_youtube_stats
from app.config import ConfigError, load_config

app = FastAPI(
    title="social-stats",
    version="1.0.0",
    description="Simple local API for social network public stats using free/public endpoints.",
)

CONFIG_PATH = Path(os.getenv("CONFIG_PATH", "config.yaml"))
CACHE = InMemoryCache()


def _get_config():
    try:
        return load_config(CONFIG_PATH)
    except ConfigError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


async def _wrap_call(call: Callable[[], Awaitable[dict[str, Any]]]) -> dict[str, Any]:
    try:
        return await call()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"status": "ok"}


@app.post("/cache/clear")
async def clear_cache() -> dict[str, Any]:
    removed = await CACHE.clear()
    return {"status": "ok", "cleared_entries": removed}


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    try:
        config = load_config(CONFIG_PATH)
        config_error = None
    except ConfigError as exc:
        config = None
        config_error = str(exc)

    return _build_test_ui(config_path=str(CONFIG_PATH), config=config, config_error=config_error)


@app.get("/stats")
async def aggregate_stats() -> dict[str, Any]:
    config = _get_config()

    instagram_jobs = [
        _wrap_call(lambda username=u: _get_instagram_stats(username, config))
        for u in config.targets.instagram
    ]
    youtube_jobs = [
        _wrap_call(lambda identifier=i: _get_youtube_stats(identifier, config))
        for i in config.targets.youtube
    ]
    github_user_jobs = [
        _wrap_call(lambda username=u: _get_github_user_stats(username, config))
        for u in config.targets.github_users
    ]
    github_repo_jobs = [
        _wrap_call(lambda full_repo=r: _get_github_repo_from_full_name(full_repo, config))
        for r in config.targets.github_repos
    ]
    dockerhub_jobs = [
        _wrap_call(lambda full_image=i: _get_dockerhub_image_from_full_name(full_image, config))
        for i in config.targets.dockerhub_images
    ]

    (
        instagram_results,
        youtube_results,
        github_user_results,
        github_repo_results,
        dockerhub_results,
    ) = await asyncio.gather(
        _run_jobs(instagram_jobs),
        _run_jobs(youtube_jobs),
        _run_jobs(github_user_jobs),
        _run_jobs(github_repo_jobs),
        _run_jobs(dockerhub_jobs),
    )

    return {
        "config_path": str(CONFIG_PATH),
        "instagram": instagram_results,
        "youtube": youtube_results,
        "github_users": github_user_results,
        "github_repos": github_repo_results,
        "dockerhub_images": dockerhub_results,
    }


@app.get("/stats/instagram/{username}")
async def instagram_stats(username: str) -> dict[str, Any]:
    config = _get_config()
    try:
        return await _get_instagram_stats(username, config)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/stats/youtube/{identifier}")
async def youtube_stats(identifier: str) -> dict[str, Any]:
    config = _get_config()
    try:
        return await _get_youtube_stats(identifier, config)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/stats/github/user/{username}")
async def github_user_stats(username: str) -> dict[str, Any]:
    config = _get_config()
    try:
        return await _get_github_user_stats(username, config)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/stats/github/repo/{owner}/{repo}")
async def github_repo_stats(owner: str, repo: str) -> dict[str, Any]:
    config = _get_config()
    try:
        return await _get_github_repo_stats(owner, repo, config)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/stats/dockerhub/{namespace}/{image}")
async def dockerhub_image_stats(namespace: str, image: str) -> dict[str, Any]:
    config = _get_config()
    try:
        return await _get_dockerhub_image_stats(namespace, image, config)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=404, detail=str(exc)) from exc


async def _get_instagram_stats(username: str, config: Any) -> dict[str, Any]:
    timeout = config.server.timeout_seconds
    return await _cache_get(
        key=f"instagram:{username.lower()}",
        config=config,
        fetcher=lambda: fetch_instagram_stats(username, timeout),
    )


async def _get_youtube_stats(identifier: str, config: Any) -> dict[str, Any]:
    timeout = config.server.timeout_seconds
    return await _cache_get(
        key=f"youtube:{identifier.lower()}",
        config=config,
        fetcher=lambda: fetch_youtube_stats(identifier, timeout),
    )


async def _get_github_user_stats(username: str, config: Any) -> dict[str, Any]:
    timeout = config.server.timeout_seconds
    return await _cache_get(
        key=f"github_user:{username.lower()}",
        config=config,
        fetcher=lambda: fetch_github_user_stats(username, timeout),
    )


async def _get_github_repo_stats(owner: str, repo: str, config: Any) -> dict[str, Any]:
    timeout = config.server.timeout_seconds
    cache_key = f"github_repo:{owner.lower()}/{repo.lower()}"
    return await _cache_get(
        key=cache_key,
        config=config,
        fetcher=lambda: fetch_github_repo_stats(owner, repo, timeout),
    )


async def _get_github_repo_from_full_name(full_repo: str, config: Any) -> dict[str, Any]:
    if "/" not in full_repo:
        raise ValueError(
            f"Invalid github repo value '{full_repo}'. Expected format owner/repo."
        )
    owner, repo = full_repo.split("/", maxsplit=1)
    return await _get_github_repo_stats(owner, repo, config)


async def _get_dockerhub_image_stats(namespace: str, image: str, config: Any) -> dict[str, Any]:
    timeout = config.server.timeout_seconds
    cache_key = f"dockerhub:{namespace.lower()}/{image.lower()}"
    return await _cache_get(
        key=cache_key,
        config=config,
        fetcher=lambda: fetch_dockerhub_image_stats(namespace, image, timeout),
    )


async def _get_dockerhub_image_from_full_name(full_image: str, config: Any) -> dict[str, Any]:
    if "/" not in full_image:
        raise ValueError(
            f"Invalid Docker Hub image value '{full_image}'. Expected format namespace/image."
        )
    namespace, image = full_image.split("/", maxsplit=1)
    return await _get_dockerhub_image_stats(namespace, image, config)


async def _cache_get(
    key: str,
    config: Any,
    fetcher: Callable[[], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    if not config.cache.enabled:
        return await fetcher()
    return await CACHE.get_or_fetch(
        key=key,
        refresh_seconds=config.cache.refresh_seconds,
        fetcher=fetcher,
    )


async def _run_jobs(jobs: Sequence[Awaitable[dict[str, Any]]]) -> list[dict[str, Any]]:
    if not jobs:
        return []
    return await asyncio.gather(*jobs)


def _build_test_ui(config_path: str, config: Any, config_error: str | None) -> str:
        config_targets: dict[str, list[str]] = {
                "instagram": [],
                "youtube": [],
                "github_users": [],
                "github_repos": [],
            "dockerhub_images": [],
        }
        if config is not None:
                config_targets = {
                        "instagram": config.targets.instagram,
                        "youtube": config.targets.youtube,
                        "github_users": config.targets.github_users,
                        "github_repos": config.targets.github_repos,
                "dockerhub_images": config.targets.dockerhub_images,
                }

        # Keep the UI self-contained to make deployment and usage simple.
        return f"""<!doctype html>
<html lang=\"en\">
    <head>
        <meta charset=\"utf-8\" />
        <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
        <title>social-stats tester</title>
        <style>
            :root {{
                --bg: #f3f5f7;
                --surface: #ffffff;
                --line: #d8dde6;
                --text: #17212b;
                --muted: #5f6b7a;
                --accent: #0e7a74;
                --accent-dark: #0a5e5a;
                --warn: #8d4f00;
            }}
            * {{ box-sizing: border-box; }}
            body {{
                margin: 0;
                background:
                    radial-gradient(circle at 100% 0%, #d7ebe8 0%, transparent 35%),
                    radial-gradient(circle at 0% 100%, #f7e7d6 0%, transparent 30%),
                    var(--bg);
                color: var(--text);
                font-family: "Trebuchet MS", "Segoe UI", sans-serif;
            }}
            .wrap {{
                max-width: 980px;
                margin: 1.2rem auto;
                padding: 0 0.8rem 1.2rem;
            }}
            .title {{
                margin: 0 0 0.4rem;
                font-family: "Palatino Linotype", Georgia, serif;
                letter-spacing: 0.02em;
            }}
            .sub {{ margin: 0; color: var(--muted); }}
            .grid {{
                margin-top: 0.9rem;
                display: grid;
                gap: 0.8rem;
                grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
            }}
            .card {{
                background: var(--surface);
                border: 1px solid var(--line);
                border-radius: 12px;
                padding: 0.85rem;
                box-shadow: 0 4px 16px rgba(0, 0, 0, 0.05);
            }}
            h2 {{ margin: 0 0 0.4rem; font-size: 1.05rem; }}
            .row {{ display: flex; gap: 0.45rem; margin: 0.35rem 0; flex-wrap: wrap; }}
            input {{
                flex: 1;
                min-width: 130px;
                border: 1px solid var(--line);
                border-radius: 8px;
                padding: 0.45rem 0.55rem;
            }}
            button {{
                border: 0;
                border-radius: 8px;
                padding: 0.44rem 0.7rem;
                background: var(--accent);
                color: #fff;
                cursor: pointer;
                font-weight: 600;
            }}
            button:hover {{ background: var(--accent-dark); }}
            .muted {{ color: var(--muted); font-size: 0.92rem; }}
            .warn {{ color: var(--warn); font-weight: 700; }}
            pre {{
                margin: 0.6rem 0 0;
                min-height: 180px;
                max-height: 48vh;
                overflow: auto;
                background: #0d1b2a;
                color: #d7e3f2;
                border-radius: 10px;
                padding: 0.75rem;
            }}
        </style>
    </head>
    <body>
        <div class=\"wrap\">
            <h1 class=\"title\">social-stats local tester</h1>
            <p class=\"sub\">Quickly test configured targets from <code>{config_path}</code> without opening API docs.</p>
            <div id=\"config-warning\" class=\"warn\">{config_error or ''}</div>

            <div class=\"grid\">
                <section class=\"card\">
                    <h2>From config.yaml</h2>
                    <div class=\"row\">
                        <button onclick=\"callApi('/health')\">Health</button>
                        <button onclick=\"callApi('/stats')\">All Config Targets</button>
                        <button onclick=\"clearCacheAndRefresh()\">Clear Cache + Refresh</button>
                    </div>
                    <div class=\"row\" id=\"buttons\"></div>
                    <p class=\"muted\">Buttons are generated from configured targets.</p>
                </section>

                <section class=\"card\">
                    <h2>Manual test</h2>
                    <div class=\"row\">
                        <input id=\"ig\" placeholder=\"instagram username\" />
                        <button onclick=\"callApi('/stats/instagram/' + encodeURIComponent(v('ig')))\">Instagram</button>
                    </div>
                    <div class="row">
                        <input id="dhi" placeholder="namespace/image" />
                        <button onclick="callDockerImage()">Docker Hub Image</button>
                    </div>
                    <div class=\"row\">
                        <input id=\"yt\" placeholder=\"youtube @handle or UC...\" />
                        <button onclick=\"callApi('/stats/youtube/' + encodeURIComponent(v('yt')))\">YouTube</button>
                    </div>
                    <div class=\"row\">
                        <input id=\"ghu\" placeholder=\"github username\" />
                        <button onclick=\"callApi('/stats/github/user/' + encodeURIComponent(v('ghu')))\">GitHub User</button>
                    </div>
                    <div class=\"row\">
                        <input id=\"ghr\" placeholder=\"owner/repo\" />
                        <button onclick=\"callRepo()\">GitHub Repo</button>
                    </div>
                </section>
            </div>

            <pre id=\"out\">Ready.</pre>
        </div>

        <script>
            const targets = {json.dumps(config_targets)};
            const out = document.getElementById('out');

            function v(id) {{
                return document.getElementById(id).value.trim();
            }}

            function addButton(label, path) {{
                const host = document.getElementById('buttons');
                const btn = document.createElement('button');
                btn.textContent = label;
                btn.onclick = () => callApi(path);
                host.appendChild(btn);
            }}

            function seedButtons() {{
                targets.instagram.forEach((u) => addButton('IG ' + u, '/stats/instagram/' + encodeURIComponent(u)));
                targets.youtube.forEach((c) => addButton('YT ' + c, '/stats/youtube/' + encodeURIComponent(c)));
                targets.github_users.forEach((u) => addButton('GH user ' + u, '/stats/github/user/' + encodeURIComponent(u)));
                targets.github_repos.forEach((r) => {{
                    const parts = r.split('/');
                    if (parts.length === 2) addButton('GH repo ' + r, '/stats/github/repo/' + encodeURIComponent(parts[0]) + '/' + encodeURIComponent(parts[1]));
                }});
                targets.dockerhub_images.forEach((i) => {{
                    const parts = i.split('/');
                    if (parts.length === 2) addButton('Docker ' + i, '/stats/dockerhub/' + encodeURIComponent(parts[0]) + '/' + encodeURIComponent(parts[1]));
                }});
            }}

            async function callApi(path) {{
                if (!path || path.endsWith('/')) {{
                    out.textContent = 'Please enter a value.';
                    return;
                }}
                out.textContent = 'Loading ' + path + ' ...';
                try {{
                    const res = await fetch(path);
                    const text = await res.text();
                    let body = text;
                    try {{ body = JSON.parse(text); }} catch (_err) {{}}
                    out.textContent = JSON.stringify({{ status: res.status, path, body }}, null, 2);
                }} catch (err) {{
                    out.textContent = String(err);
                }}
            }}

            function callRepo() {{
                const raw = v('ghr');
                const parts = raw.split('/');
                if (parts.length !== 2 || !parts[0] || !parts[1]) {{
                    out.textContent = 'Repo format must be owner/repo';
                    return;
                }}
                callApi('/stats/github/repo/' + encodeURIComponent(parts[0]) + '/' + encodeURIComponent(parts[1]));
            }}

            function callDockerImage() {{
                const raw = v('dhi');
                const parts = raw.split('/');
                if (parts.length !== 2 || !parts[0] || !parts[1]) {{
                    out.textContent = 'Docker image format must be namespace/image';
                    return;
                }}
                callApi('/stats/dockerhub/' + encodeURIComponent(parts[0]) + '/' + encodeURIComponent(parts[1]));
            }}

            async function clearCacheAndRefresh() {{
                out.textContent = 'Clearing cache ...';
                try {{
                    const clearRes = await fetch('/cache/clear', {{ method: 'POST' }});
                    const clearText = await clearRes.text();
                    let clearBody = clearText;
                    try {{ clearBody = JSON.parse(clearText); }} catch (_err) {{}}
                    out.textContent = JSON.stringify({{ status: clearRes.status, path: '/cache/clear', body: clearBody }}, null, 2);
                    await callApi('/stats');
                }} catch (err) {{
                    out.textContent = String(err);
                }}
            }}

            seedButtons();
        </script>
    </body>
</html>
"""
