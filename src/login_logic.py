from typing import Dict, Any, List, Optional

import logging
import os
import re
import time
from urllib.parse import urlsplit, urlunsplit, urlencode, quote
from fastapi.responses import HTMLResponse, JSONResponse
from authlib.integrations.starlette_client import OAuth
from starlette.requests import Request
from starlette.requests import Request as StarletteRequest
from starlette.responses import RedirectResponse

from src.bucket_identity_store import (
    empty_privileges,
    get_user_privileges,
    upsert_user_record,
)

oauth = OAuth()
timing_logger = logging.getLogger("uvicorn.error")
login_providers: List[Dict[str, Any]] = []
_DEFAULT_REDIRECT_PATH = "/recetas/"
_DEFAULT_PRIVILEGES_REFRESH_SECONDS = 120.0


def _parse_refresh_seconds(raw_value: str | None) -> float:
    try:
        return max(0.0, float(raw_value or str(_DEFAULT_PRIVILEGES_REFRESH_SECONDS)))
    except (TypeError, ValueError):
        return _DEFAULT_PRIVILEGES_REFRESH_SECONDS


_PRIVILEGES_REFRESH_SECONDS = _parse_refresh_seconds(os.getenv("USER_PRIVILEGES_REFRESH_SECONDS"))
_PRIVILEGES_REFRESH_TS_KEY = "_privileges_refreshed_at"
_ALLOWED_REDIRECT_HOSTS: tuple[str, ...] = tuple(
    host.strip().lower()
    for host in os.getenv("LOGIN_ALLOWED_REDIRECT_HOSTS", "").split(",")
    if host.strip()
)
_LOGIN_EMBED_ALLOWED_ORIGINS = tuple(
    origin.strip()
    for origin in os.getenv(
        "LOGIN_EMBED_ALLOWED_ORIGINS",
        "https://the-list.es,https://www.the-list.es,https://control.the-list.es",
    ).split(",")
    if origin.strip()
)
_EMBED_ALLOWED_ORIGIN_SET = {origin.lower() for origin in _LOGIN_EMBED_ALLOWED_ORIGINS}
_LOGIN_BUTTON_TEMPLATE = """
<div class="mt-login-wrapper" style="font-family: system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;">
  <a href="{login_url}" style="display:inline-flex;align-items:center;gap:0.5rem;padding:0.6rem 1rem;border-radius:6px;border:1px solid #d1d5db;background:#fff;color:#111827;font-weight:600;text-decoration:none;box-shadow:0 1px 2px rgba(0,0,0,0.12);">
    <span style="width:18px;height:18px;display:inline-block;"><svg viewBox="0 0 533.5 544.3" xmlns="http://www.w3.org/2000/svg"><path fill="#4285f4" d="M533.5 278.4c0-18-1.5-31.1-4.7-44.7H272.1v81.1h148.9c-3 20.5-19.4 51.4-55.9 72.1l-.5 3.2 81.2 62.4 5.6.6c51.8-47.6 81.1-117.9 81.1-174.7z"/><path fill="#34a853" d="M272.1 544.3c73.5 0 135.1-24.1 180.2-65.6l-86-66.1c-23 15.8-54 26.8-94.2 26.8-71.8 0-132.6-47.6-154.3-113.6l-3.2.3-84.2 64.9-1.1 3c44.9 89.2 137 150.3 242.8 150.3z"/><path fill="#fbbc05" d="M117.8 325.8c-5.4-16.4-8.5-34-8.5-52.2s3.1-35.8 8.2-52.2l-.1-3.5-85.4-65.8-2.8 1.3C10.3 196.2 0 231.9 0 273.6s10.3 77.4 29.2 120.1z"/><path fill="#ea4335" d="M272.1 107.7c51.2 0 85.7 22.2 105.4 40.8l77-75.1C406.4 28.7 345.6 0 272.1 0 166.3 0 74.2 61.1 29.2 150.3l88.7 69c21.7-66 82.5-111.6 154.2-111.6z"/></svg></span>
    <span>Iniciar sesión con Google</span>
  </a>
</div>
""".strip()


def make_code(value: str, default_prefix: str = "user") -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower()).strip("-")
    return normalized or default_prefix


def _log_timing(event_name: str, start: float, **fields: object) -> None:
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    if fields:
        field_text = " ".join(f"{key}={value}" for key, value in fields.items())
        timing_logger.info("login_logic.timing event=%s ms=%.2f %s", event_name, elapsed_ms, field_text)
        return
    timing_logger.info("login_logic.timing event=%s ms=%.2f", event_name, elapsed_ms)


def register_oauth_provider(*args, **kwargs):
    login_providers.append(kwargs)
    return oauth.register(*args, **kwargs)


def add_login_routes(app, app_route: str = "/recetas"):
    @app.get("/logout")
    async def logout(request: Request):
        request.session.pop("user", None)
        return RedirectResponse("/")

    @app.get(app_route)
    async def _redir_to_slash():
        return RedirectResponse(f"{app_route}/")

    for p in login_providers:
        name = p["name"]
        start_route_name = f"auth_start_{name}"
        cb_route_name = f"auth_callback_{name}"

        @app.get(f"/auth/{name}", name=start_route_name)
        async def auth_start(
            request: Request,
            redirect_to: Optional[str] = None,
            _name=name,
            _cb=cb_route_name,
            _app_route=app_route,
        ):
            client = oauth.create_client(_name)
            redirect_uri = request.url_for(_cb)
            _update_login_redirect_target(
                request,
                redirect_to,
                default_target=f"{_app_route}/" if _app_route else _DEFAULT_REDIRECT_PATH,
            )
            user = request.session.get("user")
            if user:
                _refresh_user_privileges(user)
                target = _resolve_login_redirect_target(
                    request,
                    fallback=f"{_app_route}/" if _app_route else _DEFAULT_REDIRECT_PATH,
                )
                return RedirectResponse(target)
            return await client.authorize_redirect(request, redirect_uri)

        @app.get(f"/auth/{name}/callback", name=cb_route_name)
        async def auth_callback(request: Request, _name=name, _app_route=app_route):
            client = oauth.create_client(_name)
            token = await client.authorize_access_token(request)
            userinfo = token.get("userinfo") or await client.parse_id_token(request, token)
            enriched = _persist_user(userinfo)
            request.session["user"] = enriched
            target = _resolve_login_redirect_target(
                request,
                fallback=f"{_app_route}/" if _app_route else _DEFAULT_REDIRECT_PATH,
            )
            return RedirectResponse(target)


def add_login_snippet_route(app, provider_name: str = "google"):
    """
    Register the lightweight /login endpoint that returns the embeddable login button or JSON.
    """
    state_flag = "_mt_login_snippet_registered"
    if getattr(app.state, state_flag, False):
        return

    @app.get("/login", response_class=HTMLResponse)
    async def login_snippet(  # type: ignore[func-returns-value]
        request: Request,
        redirect_to: Optional[str] = None,
        format: Optional[str] = None,
        _provider: str = provider_name,
    ):
        login_url = _build_login_url(request, _provider, redirect_to)
        html = _LOGIN_BUTTON_TEMPLATE.format(login_url=login_url)
        headers = _cors_headers(request)
        if _wants_json_response(request, format):
            return JSONResponse({"login_url": login_url, "html": html}, headers=headers)
        return HTMLResponse(html, headers=headers)

    setattr(app.state, state_flag, True)


def _should_refresh_privileges(user: Dict[str, Any]) -> bool:
    if _PRIVILEGES_REFRESH_SECONDS <= 0:
        return True
    raw_ts = user.get(_PRIVILEGES_REFRESH_TS_KEY)
    try:
        last_refreshed_at = float(raw_ts)
    except (TypeError, ValueError):
        return True
    return (time.time() - last_refreshed_at) >= _PRIVILEGES_REFRESH_SECONDS


def get_user(
    request: Any,
    *,
    refresh_privileges: bool = True,
    force_privileges_refresh: bool = False,
) -> Optional[dict]:
    total_start = time.perf_counter()
    try:
        step_start = time.perf_counter()
        if hasattr(request, "request") and hasattr(request.request, "session"):
            user = request.request.session.get("user")
        elif isinstance(request, StarletteRequest):
            user = request.session.get("user")
        else:
            user = None
        _log_timing("get_user.read_session", step_start, has_user=bool(user))
    except Exception:
        user = None
        _log_timing("get_user.read_session_error", total_start)

    if user and (refresh_privileges or force_privileges_refresh):
        if force_privileges_refresh or _should_refresh_privileges(user):
            step_start = time.perf_counter()
            _refresh_user_privileges(user)
            _store_refreshed_user_in_session(request, user)
            _log_timing(
                "get_user.refresh_privileges",
                step_start,
                ttl_seconds=_PRIVILEGES_REFRESH_SECONDS,
                forced=force_privileges_refresh,
            )
        else:
            _log_timing(
                "get_user.refresh_privileges_skipped",
                total_start,
                ttl_seconds=_PRIVILEGES_REFRESH_SECONDS,
                forced=force_privileges_refresh,
            )
    elif user:
        _log_timing(
            "get_user.refresh_privileges_disabled",
            total_start,
            forced=force_privileges_refresh,
        )
    _log_timing("get_user.total", total_start, has_user=bool(user))
    return user


def _store_refreshed_user_in_session(request: Any, user: Dict[str, Any]) -> None:
    if not user:
        return
    try:
        if hasattr(request, "request") and hasattr(request.request, "session"):
            request.request.session["user"] = user
        elif isinstance(request, StarletteRequest):
            request.session["user"] = user
    except Exception:
        # Non-fatal: keep request flow even if session persistence is unavailable.
        pass


def _persist_user(userinfo: Dict[str, Any]) -> Dict[str, Any]:
    """
    Ensure the authenticated account has a corresponding JSON record in bucket storage.
    Returns the user payload enriched with canonical identifiers and privileges.
    """
    user = dict(userinfo)
    user_id = _resolve_user_id(user)
    email = (user.get("email") or user_id or "").strip()
    code_source = (
        user.get("user_code")
        or user.get("employee_code")
        or user.get("sub")
        or user_id
    )
    code = make_code((code_source or "").strip() or user_id, default_prefix="usuario")
    display_name = (user.get("name") or "").strip() or user_id
    email = email or user_id

    stored_user = upsert_user_record(
        email=email or user_id,
        display_name=display_name,
        username=(user.get("username") or ""),
        mark_login=True,
    )
    stored_email = str(stored_user.get("email") or email or user_id).strip().lower()
    stored_name = str(stored_user.get("name") or display_name).strip() or display_name
    stored_user_id = str(stored_user.get("user_id") or stored_email).strip() or stored_email
    stored_user_code = str(stored_user.get("user_code") or code).strip() or code
    privileges = _lookup_user_privileges(stored_email)

    user["user_id"] = stored_user_id
    user["user_code"] = stored_user_code

    # Backward-compatible fields still used in parts of the codebase.
    user["employee_id"] = stored_user_id
    user["employee_code"] = stored_user_code
    user["name"] = stored_name or display_name
    user["email"] = stored_email
    user["privileges"] = privileges
    user["is_active"] = bool(stored_user.get("is_active", True))
    user[_PRIVILEGES_REFRESH_TS_KEY] = time.time()
    return user


def _empty_privileges() -> Dict[str, bool]:
    return empty_privileges()


def _lookup_user_privileges(email: str) -> Dict[str, bool]:
    total_start = time.perf_counter()
    normalized_email = (email or "").strip().lower()
    privileges = _empty_privileges()
    if normalized_email:
        query_start = time.perf_counter()
        privileges = get_user_privileges(normalized_email)
        _log_timing("lookup_user_privileges.bucket", query_start, email=normalized_email)
    _log_timing(
        "lookup_user_privileges.total",
        total_start,
        email=normalized_email or "<empty>",
        has_any=any(privileges.values()),
    )
    return privileges


def _refresh_user_privileges(user: Dict[str, Any]) -> None:
    """
    Refresh privileges from bucket and update the session cache timestamp.
    """
    if not user:
        return

    email = (user.get("email") or "").strip()
    if not email:
        return

    total_start = time.perf_counter()
    step_start = time.perf_counter()
    user["privileges"] = _lookup_user_privileges(email)
    _log_timing("refresh_user_privileges.lookup", step_start, email=email)
    user[_PRIVILEGES_REFRESH_TS_KEY] = time.time()
    _log_timing("refresh_user_privileges.total", total_start, email=email)


def _resolve_user_id(userinfo: Dict[str, Any]) -> str:
    email = (userinfo.get("email") or "").strip()
    if email:
        return email.lower()
    sub = (userinfo.get("sub") or "").strip()
    if sub:
        return sub
    name = (userinfo.get("name") or "").strip()
    if name:
        return make_code(name, default_prefix="user")
    raise ValueError("Unable to determine user identifier from login response")


def _update_login_redirect_target(request: Request, candidate: Optional[str], default_target: str) -> None:
    """
    Store a sanitized redirect target to use after login, or clear it when absent/invalid.
    """
    target = _sanitize_redirect_target(candidate, request)
    if not target:
        target = _sanitize_redirect_target(default_target, request) or default_target
    request.session["post_login_redirect"] = target


def _resolve_login_redirect_target(request: Request, fallback: str) -> str:
    target = request.session.pop("post_login_redirect", None)
    sanitized = _sanitize_redirect_target(target, request)
    if sanitized:
        return sanitized
    sanitized_fallback = _sanitize_redirect_target(fallback, request)
    return sanitized_fallback or fallback or _DEFAULT_REDIRECT_PATH


def _sanitize_redirect_target(candidate: Optional[str], request: Optional[Request]) -> Optional[str]:
    """
    Allow relative paths or whitelisted hosts; block protocol-relative / malformed URLs.
    """
    if not candidate:
        return None
    target = candidate.strip()
    if not target:
        return None
    if target.startswith("//"):
        return None
    if target.startswith("/"):
        return target

    parsed = urlsplit(target)
    if parsed.scheme not in {"https", "http"}:
        return None
    host = (parsed.hostname or "").lower()
    if not host:
        return None

    allowed_hosts: tuple[str, ...]
    if _ALLOWED_REDIRECT_HOSTS:
        allowed_hosts = _ALLOWED_REDIRECT_HOSTS
    else:
        request_host = ((request.url.hostname or "").lower() if request else "") or ""
        allowed_hosts = (request_host,) if request_host else ()

    if host not in allowed_hosts:
        return None

    # Normalize to remove any dangerous components (but leave path/query untouched)
    safe = urlunsplit(parsed)
    return safe


def _login_start_route_name(provider_name: str) -> str:
    return f"auth_start_{provider_name}"


def _build_login_url(request: Request, provider_name: str, redirect_to: Optional[str]) -> str:
    route_name = _login_start_route_name(provider_name)
    try:
        base = request.url_for(route_name)
    except Exception:
        base = str(request.base_url.replace(path=f"auth/{provider_name}"))
    params = {}
    target = (redirect_to or "").strip()
    if target:
        params["redirect_to"] = target
    if not params:
        return base
    query = urlencode(params, quote_via=quote, safe="/:")
    return f"{base}?{query}"


def _wants_json_response(request: Request, format_hint: Optional[str]) -> bool:
    if format_hint:
        return format_hint.lower() == "json"
    accept = (request.headers.get("accept") or "").lower()
    if "application/json" in accept and "text/html" not in accept:
        return True
    return False


def _cors_headers(request: Request) -> dict[str, str]:
    origin = request.headers.get("origin") or ""
    if origin and _origin_allowed(origin):
        return {"Access-Control-Allow-Origin": origin.strip()}
    return {}


def _origin_allowed(origin: str) -> bool:
    if not origin:
        return False
    normalized = origin.strip().lower()
    return normalized in _EMBED_ALLOWED_ORIGIN_SET
