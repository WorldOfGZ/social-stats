from __future__ import annotations

import asyncio
import base64
import json
import secrets
import time
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aiograpi import Client as InstagramClient
from aiograpi.exceptions import (
    BadPassword,
    ChallengeRequired,
    ClientConnectionError,
    ClientError,
    FeedbackRequired,
    LoginRequired,
    PleaseWaitFewMinutes,
    ReloginAttemptExceeded,
    TwoFactorRequired,
    UserNotFound,
)


SESSION_NOT_CONFIGURED_MESSAGE = (
    "Instagram account is not configured to crawl Instagram accounts. "
    "Open http://localhost:8000/ and log in with your Instagram account."
)

_PENDING_LOGIN_TTL_SECONDS = 600


@dataclass(slots=True)
class PendingInstagramLogin:
    client: InstagramClient
    username: str
    password: str
    session_file: Path
    created_at: float


_PENDING_LOGINS: dict[str, PendingInstagramLogin] = {}


def _cleanup_pending_logins() -> None:
    now = time.time()
    stale = [
        token
        for token, pending in _PENDING_LOGINS.items()
        if (now - pending.created_at) > _PENDING_LOGIN_TTL_SECONDS
    ]
    for token in stale:
        _PENDING_LOGINS.pop(token, None)


def _remember_pending_login(
    client: InstagramClient,
    username: str,
    password: str,
    session_file: Path,
) -> str:
    token = secrets.token_urlsafe(24)
    _PENDING_LOGINS[token] = PendingInstagramLogin(
        client=client,
        username=username,
        password=password,
        session_file=session_file,
        created_at=time.time(),
    )
    return token


def _finish_login_and_save(
    client: InstagramClient,
    config: Any,
    username: str,
    session_file: Path,
) -> dict[str, Any]:
    session_file.parent.mkdir(parents=True, exist_ok=True)
    client.dump_settings(str(session_file))
    meta_file = _session_meta_file(session_file)
    meta_file.write_text(
        json.dumps(
            {
                "username": username,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return get_instagram_session_status(config)


def _resolve_session_file(config: Any) -> Path:
    session_file = Path(getattr(config.instagram, "session_file", ".state/instagram.session")).expanduser()
    if not session_file.is_absolute():
        session_file = Path.cwd() / session_file
    return session_file


def _session_meta_file(session_file: Path) -> Path:
    return session_file.with_name(session_file.name + ".meta.json")


def _stats_snapshot_file(config: Any) -> Path:
    session_file = _resolve_session_file(config)
    return session_file.with_name("instagram.stats.snapshot.json")


def _load_stats_snapshot(config: Any, username: str) -> dict[str, Any] | None:
    snapshot_file = _stats_snapshot_file(config)
    if not snapshot_file.exists():
        return None

    try:
        raw_payload = json.loads(snapshot_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

    if not isinstance(raw_payload, dict):
        return None

    users_payload = raw_payload.get("users")
    if not isinstance(users_payload, dict):
        return None

    user_entry = users_payload.get(username.lower())
    if not isinstance(user_entry, dict):
        return None

    data = user_entry.get("data")
    fetched_at = user_entry.get("fetched_at")
    if not isinstance(data, dict) or not isinstance(fetched_at, str):
        return None

    return {
        "data": data,
        "fetched_at": fetched_at,
    }


def _save_stats_snapshot(config: Any, username: str, payload: dict[str, Any]) -> None:
    snapshot_file = _stats_snapshot_file(config)
    snapshot_file.parent.mkdir(parents=True, exist_ok=True)

    doc: dict[str, Any]
    if snapshot_file.exists():
        try:
            parsed = json.loads(snapshot_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            parsed = {}
        doc = parsed if isinstance(parsed, dict) else {}
    else:
        doc = {}

    users_payload = doc.get("users")
    if not isinstance(users_payload, dict):
        users_payload = {}
    doc["users"] = users_payload

    users_payload[username.lower()] = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "data": payload,
    }

    snapshot_file.write_text(json.dumps(doc, indent=2), encoding="utf-8")


def get_instagram_snapshot_status(config: Any, username: str | None = None) -> dict[str, Any]:
    snapshot_file = _stats_snapshot_file(config)
    result: dict[str, Any] = {
        "snapshot_file": str(snapshot_file),
        "snapshot_file_exists": snapshot_file.exists(),
        "users": {},
    }

    if not snapshot_file.exists():
        if username:
            result["requested_username"] = username
            result["found"] = False
        return result

    try:
        payload = json.loads(snapshot_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        result["error"] = "Snapshot file exists but is unreadable."
        if username:
            result["requested_username"] = username
            result["found"] = False
        return result

    users_payload = payload.get("users") if isinstance(payload, dict) else None
    if not isinstance(users_payload, dict):
        users_payload = {}

    summary_users: dict[str, Any] = {}
    for user_key, user_data in users_payload.items():
        if not isinstance(user_key, str) or not isinstance(user_data, dict):
            continue
        summary_users[user_key] = {
            "fetched_at": user_data.get("fetched_at"),
        }

    if username:
        requested = username.lower()
        result["requested_username"] = username
        result["found"] = requested in summary_users
        result["users"] = {requested: summary_users[requested]} if requested in summary_users else {}
        return result

    result["users"] = summary_users
    return result


def get_instagram_session_status(config: Any) -> dict[str, Any]:
    _cleanup_pending_logins()
    session_file = _resolve_session_file(config)
    meta_file = _session_meta_file(session_file)
    username = ""
    updated_at = None

    if meta_file.exists():
        try:
            metadata = json.loads(meta_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            metadata = {}
        username = str(metadata.get("username", "")).strip()
        updated_at = metadata.get("updated_at")

    configured = session_file.exists() and meta_file.exists() and bool(username)
    return {
        "configured": configured,
        "username": username,
        "session_file": str(session_file),
        "metadata_file": str(meta_file),
        "session_file_exists": session_file.exists(),
        "metadata_exists": meta_file.exists(),
        "pending_logins": len(_PENDING_LOGINS),
        "updated_at": updated_at,
        "message": None if configured else SESSION_NOT_CONFIGURED_MESSAGE,
    }


async def begin_instagram_session_login(
    config: Any,
    username: str,
    password: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    username = username.strip()
    if not username:
        raise ValueError("Instagram username is required.")
    if not password:
        raise ValueError("Instagram password is required.")

    session_file = _resolve_session_file(config)
    cl = InstagramClient()

    try:
        await cl.login(username, password)
    except TwoFactorRequired:
        token = _remember_pending_login(cl, username, password, session_file)
        return {
            "requires_two_factor": True,
            "pending_token": token,
            "username": username,
            "message": "Instagram needs a 2FA code. Enter it in the UI to finish creating the session.",
        }
    except BadPassword:
        raise ValueError("Invalid Instagram username or password.")
    except ChallengeRequired as exc:
        raise ValueError(
            f"Instagram requires account verification for '{username}'. "
            f"Log in from the official app or browser first, then retry: {exc}"
        ) from exc
    except (ReloginAttemptExceeded, FeedbackRequired, PleaseWaitFewMinutes) as exc:
        raise ValueError(
            f"Instagram temporarily blocked the login for '{username}'. Wait and try again: {exc}"
        ) from exc
    except ClientConnectionError as exc:
        raise ValueError(f"Instagram connection error for '{username}': {exc}") from exc
    except ClientError as exc:
        raise ValueError(f"Instagram login failed for '{username}': {exc}") from exc

    return _finish_login_and_save(cl, config, username, session_file)


async def complete_instagram_two_factor_login(
    config: Any,
    pending_token: str,
    two_factor_code: str,
) -> dict[str, Any]:
    _cleanup_pending_logins()
    pending_token = pending_token.strip()
    two_factor_code = two_factor_code.strip()
    if not pending_token:
        raise ValueError("Instagram pending token is required.")
    if not two_factor_code:
        raise ValueError("Instagram 2FA code is required.")

    pending = _PENDING_LOGINS.get(pending_token)
    if pending is None:
        raise ValueError("Instagram login request expired. Start again from the UI.")

    try:
        await pending.client.login(
            pending.username, pending.password, verification_code=two_factor_code
        )
    except BadPassword:
        raise ValueError("Invalid Instagram 2FA code.")
    except ClientError as exc:
        raise ValueError(f"Instagram 2FA login failed for '{pending.username}': {exc}") from exc
    finally:
        _PENDING_LOGINS.pop(pending_token, None)

    return _finish_login_and_save(pending.client, config, pending.username, pending.session_file)


def store_instagram_session(config: Any, username: str, session_file_base64: str) -> dict[str, Any]:
    username = username.strip()
    if not username:
        raise ValueError("Instagram username is required.")
    if not session_file_base64.strip():
        raise ValueError("Instagram session file is required.")

    session_file = _resolve_session_file(config)
    meta_file = _session_meta_file(session_file)
    session_file.parent.mkdir(parents=True, exist_ok=True)

    try:
        session_bytes = base64.b64decode(session_file_base64, validate=True)
    except ValueError as exc:
        raise ValueError("Instagram session file must be valid base64.") from exc

    session_file.write_bytes(session_bytes)
    meta_file.write_text(
        json.dumps(
            {
                "username": username,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    return get_instagram_session_status(config)


def clear_instagram_session(config: Any) -> dict[str, Any]:
    session_file = _resolve_session_file(config)
    meta_file = _session_meta_file(session_file)

    removed = False
    for path in (session_file, meta_file):
        if path.exists():
            path.unlink()
            removed = True

    status = get_instagram_session_status(config)
    status["removed"] = removed
    return status


async def fetch_instagram_stats(username: str, timeout_seconds: int, config: Any) -> dict[str, Any]:
    status = get_instagram_session_status(config)
    if not status["configured"]:
        raise ValueError(SESSION_NOT_CONFIGURED_MESSAGE)

    fetch_error: ValueError | None = None

    try:
        cl = InstagramClient()
        session_file = Path(status["session_file"])
        try:
            cl.load_settings(str(session_file))
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            raise ValueError(
                "Instagram session file is in an incompatible format (old instaloader session). "
                "Open http://localhost:8000/, click 'Clear Session', then log in again."
            )

        async with asyncio.timeout(float(timeout_seconds)):
            user = await cl.user_info_by_username(username)

        result = {
            "platform": "instagram",
            "username": user.username,
            "private": user.is_private,
            "followers": user.follower_count,
            "following": user.following_count,
            "posts": user.media_count,
            "profile_url": f"https://www.instagram.com/{user.username}/",
            "source": "instagrapi_private_api",
        }

        _save_stats_snapshot(config, result["username"], result)
        cl.dump_settings(str(session_file))
        return result

    except (asyncio.TimeoutError, TimeoutError):
        raise ValueError(f"Instagram fetch timed out for '{username}' after {timeout_seconds}s.")
    except UserNotFound:
        raise ValueError(f"Instagram user not found: {username}")
    except LoginRequired:
        raise ValueError(
            "Instagram session is not configured or has expired. "
            "Open http://localhost:8000/ and log in again."
        )
    except (PleaseWaitFewMinutes, FeedbackRequired) as exc:
        fetch_error = ValueError(
            f"Instagram rate limited the request for '{username}'. "
            f"Wait and try again later, or use a different IP/network: {exc}"
        )
    except ChallengeRequired as exc:
        fetch_error = ValueError(
            f"Instagram requires account verification. "
            f"Log in from the official app or browser to resolve the challenge: {exc}"
        )
    except ClientConnectionError as exc:
        fetch_error = ValueError(f"Instagram connection error for '{username}': {exc}")
    except ClientError as exc:
        msg = str(exc).lower()
        if "not found" in msg or "user not found" in msg:
            raise ValueError(f"Instagram user not found: {username}")
        fetch_error = ValueError(f"Instagram error for '{username}': {exc}")

    assert fetch_error is not None
    snapshot = _load_stats_snapshot(config, username)
    if snapshot is not None:
        payload = dict(snapshot["data"])
        payload["stale"] = True
        payload["last_fresh_crawl"] = snapshot["fetched_at"]
        payload["fallback_reason"] = str(fetch_error)
        payload["source"] = "instagram_persistent_snapshot_fallback"
        return payload
    raise fetch_error
