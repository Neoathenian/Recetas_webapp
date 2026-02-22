# src/mount_gradio_app.py
import logging
import os
import re
from pathlib import Path

from starlette.requests import Request
from starlette.responses import RedirectResponse
import gradio as gr
from starlette.middleware.sessions import SessionMiddleware

from src.login_logic import add_login_routes, get_user
from src.secrets import get_secret
from src.privileges import (
    page_key_for_route,
    user_can_access_page,
    default_page_path,
)

logger = logging.getLogger(__name__)

GRADIO_PUBLIC_PREFIXES = (
    "/gradio_api", "/file", "/assets", "/static", "/config",
    "/proxy", "/localfiles", "/theme.css", "/favicon.ico",
    "/robots.txt", "/logo.png", "/images",
)

# Public non-auth endpoints (none for the timesheet app)
PUBLIC_EXTRA: tuple[str, ...] = ()

_GRADIO_UPLOAD_PATCH_APPLIED = False


def _resolve_gradio_upload_chunk_size() -> int:
    raw_value = str(os.getenv("THE_LIST_GRADIO_UPLOAD_CHUNK_SIZE", "1")).strip()
    try:
        parsed = int(raw_value)
    except (TypeError, ValueError):
        logger.warning(
            "Invalid THE_LIST_GRADIO_UPLOAD_CHUNK_SIZE=%r; using default 1.",
            raw_value,
        )
        return 1
    return max(1, min(1000, parsed))


def _candidate_gradio_js_assets() -> list[Path]:
    package_root = Path(gr.__file__).resolve().parent
    candidates: list[Path] = [package_root / "_frontend_code" / "client" / "dist" / "index.js"]

    frontend_assets = package_root / "templates" / "frontend" / "assets"
    if frontend_assets.exists():
        candidates.extend(sorted(frontend_assets.glob("index-*.js")))

    node_server_chunks = package_root / "templates" / "node" / "build" / "server" / "chunks"
    if node_server_chunks.exists():
        candidates.extend(sorted(node_server_chunks.glob("*.js")))

    node_client_chunks = package_root / "templates" / "node" / "build" / "client" / "_app" / "immutable" / "chunks"
    if node_client_chunks.exists():
        candidates.extend(sorted(node_client_chunks.glob("*.js")))

    deduped: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        if not path.is_file():
            continue
        key = str(path.resolve())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(path)
    return deduped


def _patch_gradio_upload_chunk_size() -> None:
    global _GRADIO_UPLOAD_PATCH_APPLIED
    if _GRADIO_UPLOAD_PATCH_APPLIED:
        return
    _GRADIO_UPLOAD_PATCH_APPLIED = True

    chunk_size = _resolve_gradio_upload_chunk_size()
    if chunk_size >= 1000:
        return

    # Gradio 5 bundles the upload client in prebuilt JS. We rewrite its per-request
    # batch size to avoid proxy/platform body-size 413 errors on directory uploads.
    chunk_pattern = re.compile(r"const chunkSize\s*=\s*(?:\d+(?:e\d+)?)\s*;")
    minified_pattern = re.compile(
        r"const n=(?:\d+(?:e\d+)?),i=\[\];let a;for\(let s=0;s<t\.length;s\+=n\)\{"
    )
    chunk_replacement = f"const chunkSize = {chunk_size};"
    minified_replacement = f"const n={chunk_size},i=[];let a;for(let s=0;s<t.length;s+=n){{"

    patched_files = 0
    for asset_path in _candidate_gradio_js_assets():
        try:
            original = asset_path.read_text(encoding="utf-8")
        except OSError:
            continue

        updated = chunk_pattern.sub(chunk_replacement, original)
        updated = minified_pattern.sub(minified_replacement, updated)
        if updated == original:
            continue

        try:
            asset_path.write_text(updated, encoding="utf-8")
            patched_files += 1
        except OSError:
            logger.warning("Could not patch Gradio asset %s", asset_path, exc_info=True)

    if patched_files:
        logger.info(
            "Patched Gradio upload batching to %s files/request across %s asset file(s).",
            chunk_size,
            patched_files,
        )
    else:
        logger.warning(
            "Did not patch any Gradio upload asset files; upload batching may remain at Gradio defaults."
        )

def add_middleware_redirect(app, app_route: str):
    """
    Protect everything under `app_route`, requiring both authentication and the proper privilege.
    Root-level Gradio internals and PUBLIC_EXTRA remain available without a session.
    """
    route_no_slash = app_route or "/"
    if not route_no_slash.startswith("/"):
        route_no_slash = f"/{route_no_slash}"
    route_no_slash = route_no_slash.rstrip("/") or "/"
    route_prefix = f"{route_no_slash}/" if route_no_slash != "/" else "/"
    page_key_route = page_key_for_route(route_no_slash)

    def _matches_protected_path(path: str) -> bool:
        if route_no_slash == "/":
            return True
        normalized_path = path or "/"
        if not normalized_path.startswith("/"):
            normalized_path = f"/{normalized_path}"
        if normalized_path != "/" and normalized_path.endswith("/"):
            normalized_path = normalized_path.rstrip("/")
        if normalized_path == route_no_slash:
            return True
        return normalized_path.startswith(route_prefix)

    @app.middleware("http")
    async def check_authentication(request: Request, call_next):
        path = request.url.path
        user = None
        privileges = None

        def _ensure_user_loaded(*, force_privilege_refresh: bool = False):
            nonlocal user, privileges
            if user is None:
                # Middleware can use session-cached privileges; avoid DB refresh per heartbeat/poll request.
                user = get_user(request, refresh_privileges=False)
                privileges = (user or {}).get("privileges")
            if force_privilege_refresh and user:
                user = get_user(
                    request,
                    refresh_privileges=True,
                    force_privileges_refresh=True,
                )
                privileges = (user or {}).get("privileges")
            return user

        # If user is already authenticated and hits root, send to app
        if path == "/" and not request.query_params.get("home"):
            _ensure_user_loaded()
            if user:
                redirect_target = default_page_path(privileges)
                return RedirectResponse(url=redirect_target)

        # Always allow public root and auth/public entry points
        if (
            path == "/" or
            path.startswith("/auth") or
            path.startswith("/login") or
            any(path.startswith(p) for p in GRADIO_PUBLIC_PREFIXES) or
            any(path.startswith(p) for p in PUBLIC_EXTRA)
        ):
            return await call_next(request)

        # Require session for protected mount pages
        if _matches_protected_path(path):
            # Allow explicitly public pages without requiring a session.
            if page_key_route and user_can_access_page(None, page_key_route):
                return await call_next(request)
            _ensure_user_loaded()
            if not user:
                return RedirectResponse(url="/")
            if page_key_route and not user_can_access_page(privileges, page_key_route):
                _ensure_user_loaded(force_privilege_refresh=True)
                if not user:
                    return RedirectResponse(url="/")
            if page_key_route and not user_can_access_page(privileges, page_key_route):
                redirect_target = default_page_path(privileges)
                return RedirectResponse(url=redirect_target)
            return await call_next(request)

        # Non-matching paths: pass through
        return await call_next(request)

def mount_gradio_app(*args, secret_key: str | None = None, **kwargs):
    app = args[0]
    path = args[2]

    _patch_gradio_upload_chunk_size()
    add_middleware_redirect(app, path)
    add_login_routes(app, path)

    # session secret via get_secret (env locally, Secret Manager on GCP)
    secret = secret_key or get_secret("SESSION_SECRET", default="dev-session-secret")
    app.add_middleware(SessionMiddleware, secret_key=secret)

    return gr.mount_gradio_app(*args, **kwargs)
