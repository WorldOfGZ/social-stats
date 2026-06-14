from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Sequence

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

from app.cache import InMemoryCache
from app.clients.dockerhub_client import fetch_dockerhub_image_stats
from app.clients.github_client import fetch_github_repo_stats, fetch_github_user_stats
from app.clients.youtube_client import fetch_youtube_stats
from app.config import ConfigError, load_config
from app.clients.instagram_client import (
    clear_instagram_session,
    begin_instagram_session_login,
    complete_instagram_two_factor_login,
    fetch_instagram_stats,
    get_instagram_snapshot_status,
    get_instagram_session_status,
)

app = FastAPI(
    title="social-stats",
    version="1.0.0",
    description="Simple local API for social network public stats using free/public endpoints.",
)

CONFIG_PATH = Path(os.getenv("CONFIG_PATH", "config.yaml"))
CACHE = InMemoryCache()

# Per-service cache TTLs (seconds).
_TTL_INSTAGRAM = 86400
_TTL_YOUTUBE = 3600
_TTL_GITHUB_USERS = 3600
_TTL_GITHUB_REPOS = 3600
_TTL_DOCKERHUB = 7200
_MIN_INSTAGRAM_TIMEOUT_SECONDS = 30


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

    instagram_status = get_instagram_session_status(config) if config is not None else None
    return _build_test_ui(
        config_path=str(CONFIG_PATH),
        config=config,
        config_error=config_error,
        instagram_status=instagram_status,
    )


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
    except ValueError as exc:
        detail = str(exc)
        lowered = detail.lower()
        if "upstream request failed" in lowered or "connection error" in lowered or "invalid request" in lowered:
            raise HTTPException(status_code=502, detail=detail) from exc
        if "not configured" in lowered:
            raise HTTPException(status_code=503, detail=detail) from exc
        if "timed out" in lowered:
            raise HTTPException(status_code=504, detail=detail) from exc
        if "private" in lowered:
            raise HTTPException(status_code=403, detail=detail) from exc
        if "rate limited" in lowered:
            raise HTTPException(status_code=429, detail=detail) from exc
        if "requires login" in lowered:
            raise HTTPException(status_code=401, detail=detail) from exc
        raise HTTPException(status_code=404, detail=detail) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


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
    timeout = max(config.server.timeout_seconds, _MIN_INSTAGRAM_TIMEOUT_SECONDS)
    return await _cache_get(
        key=f"instagram:{username.lower()}",
        ttl=_TTL_INSTAGRAM,
        config=config,
        fetcher=lambda: fetch_instagram_stats(username, timeout, config),
        allow_stale_on_error=True,
    )


async def _get_youtube_stats(identifier: str, config: Any) -> dict[str, Any]:
    timeout = config.server.timeout_seconds
    return await _cache_get(
        key=f"youtube:{identifier.lower()}",
        ttl=_TTL_YOUTUBE,
        config=config,
        fetcher=lambda: fetch_youtube_stats(identifier, timeout),
    )


async def _get_github_user_stats(username: str, config: Any) -> dict[str, Any]:
    timeout = config.server.timeout_seconds
    return await _cache_get(
        key=f"github_user:{username.lower()}",
        ttl=_TTL_GITHUB_USERS,
        config=config,
        fetcher=lambda: fetch_github_user_stats(username, timeout),
    )


async def _get_github_repo_stats(owner: str, repo: str, config: Any) -> dict[str, Any]:
    timeout = config.server.timeout_seconds
    cache_key = f"github_repo:{owner.lower()}/{repo.lower()}"
    return await _cache_get(
        key=cache_key,
        ttl=_TTL_GITHUB_REPOS,
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
        ttl=_TTL_DOCKERHUB,
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
    ttl: int | None = None,
    allow_stale_on_error: bool = False,
) -> dict[str, Any]:
    if not config.cache.enabled:
        value = await fetcher()
        payload = dict(value)
        payload["last_fresh_crawl"] = datetime.now(timezone.utc).isoformat()
        payload["stale"] = False
        return payload
    return await CACHE.get_or_fetch(
        key=key,
        refresh_seconds=ttl if ttl is not None else config.cache.refresh_seconds,
        fetcher=fetcher,
        allow_stale_on_error=allow_stale_on_error,
    )



@app.get("/instagram/session/status")
async def instagram_session_status() -> dict[str, Any]:
    config = _get_config()
    return get_instagram_session_status(config)


@app.post("/instagram/session/login")
async def instagram_session_login(payload: dict[str, Any]) -> JSONResponse:
    config = _get_config()
    try:
        pending_token = str(payload.get("pending_token", "")).strip()
        two_factor_code = str(payload.get("two_factor_code", "")).strip()
        if pending_token and two_factor_code:
            result = await complete_instagram_two_factor_login(
                config,
                pending_token=pending_token,
                two_factor_code=two_factor_code,
            )
            return JSONResponse(status_code=200, content=result)

        username = str(payload.get("username", "")).strip()
        password = str(payload.get("password", ""))
        result = await begin_instagram_session_login(
            config,
            username=username,
            password=password,
            timeout_seconds=config.server.timeout_seconds,
        )
        if result.get("requires_two_factor"):
            return JSONResponse(status_code=202, content=result)
        return JSONResponse(status_code=200, content=result)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/instagram/session")
async def delete_instagram_session() -> dict[str, Any]:
    config = _get_config()
    return clear_instagram_session(config)


@app.get("/instagram/snapshot/status")
async def instagram_snapshot_status() -> dict[str, Any]:
    config = _get_config()
    return get_instagram_snapshot_status(config)


@app.get("/instagram/snapshot/status/{username}")
async def instagram_snapshot_status_for_user(username: str) -> dict[str, Any]:
    config = _get_config()
    return get_instagram_snapshot_status(config, username=username)


async def _run_jobs(jobs: Sequence[Awaitable[dict[str, Any]]]) -> list[dict[str, Any]]:
    if not jobs:
        return []
    return await asyncio.gather(*jobs)


def _build_test_ui(
    config_path: str,
    config: Any,
    config_error: str | None,
    instagram_status: dict[str, Any] | None,
) -> str:
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

    instagram_status_json = json.dumps(instagram_status or {})

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
                    <h2>Instagram session</h2>
                    <div id=\"instagram-warning\" class=\"warn\"></div>
                    <div class=\"row\">
                            <input id="ig-user" placeholder="instagram username" />
                    </div>
                    <div class=\"row\">
                            <input id="ig-pass" type="password" placeholder="instagram password" />
                        </div>
                        <div class="row">
                            <input id="ig-2fa" placeholder="2FA code, if needed" />
                    </div>
                    <div class=\"row\">
                            <button onclick="startInstagramLogin()">Login</button>
                            <button onclick="completeInstagramTwoFactor()">Complete 2FA</button>
                        <button onclick=\"refreshInstagramSessionStatus()\">Refresh Status</button>
                        <button onclick=\"clearInstagramSession()\">Clear Session</button>
                    </div>
                        <p class="muted">Log in here to create the Instagram session directly. It is stored under <code>.state/</code> and ignored by git.</p>
                </section>

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
                    <div class=\"row\">
                        <input id=\"dhi\" placeholder=\"namespace/image\" />
                        <button onclick=\"callDockerImage()\">Docker Hub Image</button>
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
            const instagramStatus = {instagram_status_json};
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

            function renderInstagramStatus(status) {{
                const box = document.getElementById('instagram-warning');
                if (!box) {{
                    return;
                }}
                if (!status || !status.configured) {{
                        const pending = status && status.pending_logins ? ' A login step may still be pending.' : '';
                        box.textContent = (status && status.message) ? status.message + pending : 'Instagram crawling is not configured. Use the login form above.';
                    return;
                }}
                    const pendingNote = status.pending_logins ? ' Pending login steps: ' + status.pending_logins + '.' : '';
                    box.textContent = 'Instagram session loaded for @' + status.username + '. Last refreshed: ' + (status.updated_at || 'unknown') + '.' + pendingNote;
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

            async function startInstagramLogin() {{
                const username = v('ig-user');
                const password = v('ig-pass');
                if (!username || !password) {{
                    out.textContent = 'Enter an Instagram username and password.';
                    return;
                }}
                out.textContent = 'Logging in to Instagram ...';
                try {{
                    const response = await fetch('/instagram/session/login', {{
                        method: 'POST',
                        headers: {{ 'Content-Type': 'application/json' }},
                        body: JSON.stringify({{ username, password }}),
                    }});
                    const text = await response.text();
                    let body = text;
                    try {{ body = JSON.parse(text); }} catch (_err) {{}}
                    const result = {{ status: response.status, path: '/instagram/session/login', body }};
                    out.textContent = JSON.stringify(result, null, 2);
                    if (response.ok || response.status === 202) {{
                        instagramStatus.pending_token = body.pending_token || '';
                        instagramStatus.username = body.username || username;
                        renderInstagramStatus(body);
                        if (body.requires_two_factor) {{
                            document.getElementById('ig-2fa').focus();
                        }}
                    }}
                }} catch (err) {{
                    out.textContent = String(err);
                }}
            }}

            async function completeInstagramTwoFactor() {{
                const pendingToken = instagramStatus.pending_token || '';
                const twoFactorCode = v('ig-2fa');
                if (!pendingToken) {{
                    out.textContent = 'Start the Instagram login first.';
                    return;
                }}
                if (!twoFactorCode) {{
                    out.textContent = 'Enter the 2FA code from Instagram.';
                    return;
                }}
                out.textContent = 'Completing Instagram 2FA ...';
                try {{
                    const response = await fetch('/instagram/session/login', {{
                        method: 'POST',
                        headers: {{ 'Content-Type': 'application/json' }},
                        body: JSON.stringify({{ pending_token: pendingToken, two_factor_code: twoFactorCode }}),
                    }});
                    const text = await response.text();
                    let body = text;
                    try {{ body = JSON.parse(text); }} catch (_err) {{}}
                    const result = {{ status: response.status, path: '/instagram/session/login', body }};
                    out.textContent = JSON.stringify(result, null, 2);
                    if (response.ok) {{
                        instagramStatus.pending_token = '';
                        renderInstagramStatus(body);
                    }}
                }} catch (err) {{
                    out.textContent = String(err);
                }}
            }}

            async function clearInstagramSession() {{
                out.textContent = 'Clearing Instagram session ...';
                try {{
                    const response = await fetch('/instagram/session', {{ method: 'DELETE' }});
                    const text = await response.text();
                    let body = text;
                    try {{ body = JSON.parse(text); }} catch (_err) {{}}
                    const result = {{ status: response.status, path: '/instagram/session', body }};
                    out.textContent = JSON.stringify(result, null, 2);
                    Object.assign(instagramStatus, body);
                    renderInstagramStatus(body);
                }} catch (err) {{
                    out.textContent = String(err);
                }}
            }}

            async function refreshInstagramSessionStatus() {{
                out.textContent = 'Refreshing Instagram session status ...';
                try {{
                    const response = await fetch('/instagram/session/status');
                    const text = await response.text();
                    let body = text;
                    try {{ body = JSON.parse(text); }} catch (_err) {{}}
                    out.textContent = JSON.stringify({{ status: response.status, path: '/instagram/session/status', body }}, null, 2);
                    if (response.ok) {{
                        Object.assign(instagramStatus, body);
                        renderInstagramStatus(body);
                    }}
                }} catch (err) {{
                    out.textContent = String(err);
                }}
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
                    await refreshInstagramSessionStatus();
                }} catch (err) {{
                    out.textContent = String(err);
                }}
            }}

            seedButtons();
            renderInstagramStatus(instagramStatus);
        </script>
    </body>
</html>
"""
