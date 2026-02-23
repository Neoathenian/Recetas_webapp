# ---- Resolve & inject ALL secrets BEFORE importing modules that read env ----
from src.secrets import get_secret

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse, Response
from starlette.staticfiles import StaticFiles
from datetime import datetime, timezone
from email.utils import format_datetime, parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote


def _install_proxy_headers(app: FastAPI) -> None:
    """Attach a proxy-aware middleware even on stripped Starlette builds."""

    try:
        from starlette.middleware.proxy_headers import ProxyHeadersMiddleware as _Proxy

        app.add_middleware(_Proxy)
        return
    except ImportError:
        pass

    try:
        from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware as _Proxy  # type: ignore

        app.add_middleware(_Proxy, trusted_hosts="*")
        return
    except ImportError:
        pass

    from starlette.middleware.base import BaseHTTPMiddleware

    class _Proxy(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            forwarded_proto = request.headers.get("x-forwarded-proto")
            if forwarded_proto:
                request.scope["scheme"] = forwarded_proto.split(",")[0].strip()

            forwarded_host = request.headers.get("x-forwarded-host")
            forwarded_port = request.headers.get("x-forwarded-port")
            server = request.scope.get("server", (None, None))

            host = forwarded_host.split(",")[0].strip() if forwarded_host else server[0]
            port = (
                int(forwarded_port.split(",")[0])
                if forwarded_port and forwarded_port.split(",")[0].isdigit()
                else server[1]
            )
            if host or port:
                request.scope["server"] = (host, port)

            return await call_next(request)

    app.add_middleware(_Proxy)

import os
import mimetypes
import gradio as gr

_configured_bucket = (os.getenv("BUCKET_NAME") or os.getenv("API_STORAGE_BUCKET") or "").strip()
if _configured_bucket:
    # Keep BUCKET_NAME as the source of truth for runtime config.
    os.environ["RECETAS_BUCKET_NAME"] = _configured_bucket
else:
    os.environ.setdefault("RECETAS_BUCKET_NAME", "recetas-bucket")

from src.login_logic import register_oauth_provider, add_login_snippet_route
from src.pages.ui_login import make_login_page
from src.mount_gradio_app import mount_gradio_app
from src.pages.recetas_list.app_the_list import make_the_list_app
from src.pages.recetas_display.app_people_display import make_people_display_app
from src.pages.privileges.app_privileges import make_privileges_app
from src.pages.profile.app_profile import make_profile_app

from src.gcs_storage import blob_http_metadata, download_bytes

app = FastAPI()
_install_proxy_headers(app)

MEDIA_CACHE_CONTROL_REVALIDATE = "public, max-age=0, must-revalidate"
MEDIA_CACHE_CONTROL_VERSIONED = "public, max-age=31536000, immutable"


def _make_bucket_notice_app(path: str, title: str, heading: str, message: str) -> gr.Blocks:
    from src.pages.header import render_header

    with gr.Blocks(title=title) as app_notice:
        hdr = gr.HTML()

        def _render_notice_header(request: gr.Request):
            return render_header(path=path, request=request)

        app_notice.load(_render_notice_header, outputs=[hdr])
        with gr.Column():
            gr.Markdown(f"## {heading}")
            gr.Markdown(message)
    return app_notice


def _quote_etag(raw_etag: str | None) -> str:
    value = str(raw_etag or "").strip()
    if not value:
        return ""
    if value.startswith("W/"):
        value = value[2:].strip()
    value = value.strip('"')
    if not value:
        return ""
    return f'"{value}"'


def _etag_matches(header_value: str | None, current_etag: str) -> bool:
    if not header_value or not current_etag:
        return False
    current = current_etag.removeprefix("W/").strip().strip('"')
    if not current:
        return False
    for token in str(header_value).split(","):
        candidate = token.strip()
        if not candidate:
            continue
        if candidate == "*":
            return True
        if candidate.removeprefix("W/").strip().strip('"') == current:
            return True
    return False


def _parse_http_date(header_value: str | None) -> datetime | None:
    if not header_value:
        return None
    try:
        parsed = parsedate_to_datetime(header_value)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _format_http_date(value: datetime | None) -> str:
    if value is None:
        return ""
    return format_datetime(value.astimezone(timezone.utc).replace(microsecond=0), usegmt=True)


def _is_not_modified(
    request: Request,
    *,
    etag: str,
    updated_at: datetime | None,
) -> bool:
    if_none_match = request.headers.get("if-none-match")
    if _etag_matches(if_none_match, etag):
        return True

    if if_none_match:
        return False

    if_modified_since = _parse_http_date(request.headers.get("if-modified-since"))
    if if_modified_since is None or updated_at is None:
        return False
    return updated_at.astimezone(timezone.utc).replace(microsecond=0) <= if_modified_since

@app.get("/_routes")
def _routes():
    return [getattr(r, "path", str(r)) for r in app.router.routes]


@app.middleware("http")
async def redirect_legacy_routes(request: Request, call_next):
    normalized_path = (request.url.path or "/").rstrip("/") or "/"
    if normalized_path == "/recetas":
        slug = str(request.query_params.get("slug", "")).strip().lower()
        if slug:
            target = f"/receta/?slug={quote(slug, safe='-')}"
            return RedirectResponse(url=target, status_code=307)
    if normalized_path == "/the-list":
        slug = str(request.query_params.get("slug", "")).strip().lower()
        if slug:
            target = f"/receta/?slug={quote(slug, safe='-')}"
            return RedirectResponse(url=target, status_code=307)
        return RedirectResponse(url="/recetas/", status_code=307)
    if normalized_path == "/people-display":
        slug = str(request.query_params.get("slug", "")).strip().lower()
        if slug:
            target = f"/receta/?slug={quote(slug, safe='-')}"
            return RedirectResponse(url=target, status_code=307)
        return RedirectResponse(url="/receta/", status_code=307)
    if normalized_path == "/the-list-review":
        query = str(request.url.query or "").strip()
        target = f"/review/?{query}" if query else "/review/"
        return RedirectResponse(url=target, status_code=307)
    removed_sections = {
        "/theories",
        "/sources",
        "/source-create",
        "/sources-individual",
        "/unsorted-files",
        "/theory-display",
        "/theory-create",
        "/people-create",
    }
    if normalized_path in removed_sections:
        return RedirectResponse(url="/recetas/", status_code=307)
    return await call_next(request)

# OAuth client config (now guaranteed in env; also available via get_secret)
GOOGLE_CLIENT_ID     = get_secret("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = get_secret("GOOGLE_CLIENT_SECRET")

register_oauth_provider(
    name="google",
    icon="google",
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_id=GOOGLE_CLIENT_ID,
    client_secret=GOOGLE_CLIENT_SECRET,
    client_kwargs={
        "scope": "openid email profile",
        "timeout": 30,
    },
)
add_login_snippet_route(app, provider_name="google")

# --- Static assets
os.makedirs("images", exist_ok=True)
FAVICON_FILE = Path("images") / "The-list-logo2.png"
app.mount(
    "/images",
    StaticFiles(directory="images", check_dir=False),
    name="images",
)


@app.get("/favicon.ico")
async def favicon() -> FileResponse:
    if FAVICON_FILE.exists():
        return FileResponse(FAVICON_FILE)
    raise HTTPException(status_code=404)


@app.get("/media/{blob_path:path}")
async def media_blob(blob_path: str, request: Request) -> Response:
    normalized = (blob_path or "").strip().lstrip("/")
    if not normalized:
        raise HTTPException(status_code=404)

    # Versioned URLs (`?v=...`) are content-addressed from app data, so we can cache
    # aggressively and skip metadata round-trips.
    version_token = str(request.query_params.get("v", "")).strip()
    if version_token:
        guessed_type = mimetypes.guess_type(normalized)[0]
        try:
            payload = download_bytes(normalized)
        except FileNotFoundError:
            raise HTTPException(status_code=404)
        except Exception:
            raise HTTPException(status_code=500, detail="Media fetch failed")
        return Response(
            content=payload,
            media_type=guessed_type or "application/octet-stream",
            headers={"Cache-Control": MEDIA_CACHE_CONTROL_VERSIONED},
        )

    try:
        content_type, blob_etag, blob_updated_at = blob_http_metadata(normalized)
    except FileNotFoundError:
        raise HTTPException(status_code=404)
    except Exception:
        raise HTTPException(status_code=500, detail="Media fetch failed")

    etag = _quote_etag(blob_etag)
    last_modified = _format_http_date(blob_updated_at)
    headers = {
        "Cache-Control": MEDIA_CACHE_CONTROL_REVALIDATE,
    }
    if etag:
        headers["ETag"] = etag
    if last_modified:
        headers["Last-Modified"] = last_modified

    if _is_not_modified(request, etag=etag, updated_at=blob_updated_at):
        return Response(status_code=304, headers=headers)

    try:
        payload = download_bytes(normalized)
    except FileNotFoundError:
        raise HTTPException(status_code=404)
    except Exception:
        raise HTTPException(status_code=500, detail="Media fetch failed")

    return Response(content=payload, media_type=content_type or "application/octet-stream", headers=headers)

# --- Simple pages
the_list_app   = make_the_list_app()
people_display_app = make_people_display_app()
privileges_app = make_privileges_app()
profile_app = make_profile_app()
login_page     = make_login_page()

review_display_app = _make_bucket_notice_app(
    path="/review",
    title="Revisión de Recetas",
    heading="Revisión no disponible",
    message=(
        "Esta instancia está en modo bucket (`recetas-bucket`) y no usa Cloud SQL. "
        "El flujo de revisión SQL está deshabilitado."
    ),
)
admin_app = _make_bucket_notice_app(
    path="/admin",
    title="Administración de Recetas",
    heading="Administración SQL deshabilitada",
    message=(
        "Esta instancia funciona solo con bucket (`recetas-bucket`). "
        "El explorador de tablas SQL no está disponible."
    ),
)

# Optional: session secret via secret manager (fallback default set in bootstrap)
session_secret = get_secret("SESSION_SECRET", default="dev-session-secret")
mount_gradio_app(app, the_list_app,  "/recetas", secret_key=session_secret)
mount_gradio_app(app, people_display_app,  "/receta", secret_key=session_secret)
mount_gradio_app(app, review_display_app, "/review", secret_key=session_secret)
mount_gradio_app(app, admin_app,         "/admin", secret_key=session_secret)
mount_gradio_app(app, privileges_app,   "/privileges", secret_key=session_secret)
mount_gradio_app(app, profile_app,      "/profile", secret_key=session_secret)


@app.get("/people")
@app.get("/people/")
async def legacy_people_redirect() -> RedirectResponse:
    return RedirectResponse(url="/recetas/")


gr.mount_gradio_app(app, login_page, "/")
