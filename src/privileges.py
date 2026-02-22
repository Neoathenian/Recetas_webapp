from __future__ import annotations

from dataclasses import dataclass
import os
from typing import List, Mapping, Optional, Set


@dataclass(frozen=True)
class PageLink:
    key: str
    label: str
    path: str
    css_class: str


NAVIGATION_ORDER: tuple[str, ...] = (
    "the-list",
    "the-list-review",
    "privileges",
    "admin",
)

PUBLIC_PAGE_KEYS: tuple[str, ...] = (
    "the-list",
)

PAGE_REGISTRY: dict[str, PageLink] = {
    "the-list": PageLink("the-list", "Recetas", "/recetas/", "hdr-link hdr-link--the-list"),
    "the-list-review": PageLink(
        "the-list-review",
        "Revisión",
        "/review/",
        "hdr-link hdr-link--the-list-review",
    ),
    "privileges": PageLink(
        "privileges",
        "Privilegios",
        "/privileges/",
        "hdr-link hdr-link--privileges",
    ),
    "admin": PageLink("admin", "Administración", "/admin/", "hdr-link hdr-link--admin"),
}

PRIVILEGE_PAGE_MAP: dict[str, set[str]] = {
    "base_user": {"the-list"},
    "reviewer": {"the-list", "the-list-review"},
    "editor": {"the-list"},
    "admin": {"the-list", "the-list-review", "privileges"},
    "creator": {"the-list", "the-list-review", "privileges", "admin"},
}

DEBUG_PRIVILEGES_ENV = "THELIST_DEBUG_PRIVILEGES"
_DEBUG_TRUE_VALUES = {"1", "true", "yes", "on"}

PATH_TO_PAGE_KEY: dict[str, Optional[str]] = {
    "/the-list": "the-list",
    "/recetas": "the-list",
    "/people-display": "the-list",
    "/receta": "the-list",
    "/the-list-review": "the-list-review",
    "/review": "the-list-review",
    "/admin": "admin",
    "/privileges": "privileges",
}

PrivilegeMapping = Mapping[str, bool]


def _is_truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _enabled_privilege_keys(privileges: PrivilegeMapping | None) -> tuple[str, ...]:
    if not privileges:
        return ()
    normalized: list[str] = []
    for key, enabled in privileges.items():
        if not enabled:
            continue
        normalized_key = (key or "").strip().lower()
        if not normalized_key:
            continue
        normalized.append(normalized_key)
    return tuple(normalized)


def resolve_nav_links(privileges: PrivilegeMapping | None) -> List[PageLink]:
    if _is_debug_mode():
        return [PAGE_REGISTRY[key] for key in NAVIGATION_ORDER]

    allowed: set[str] = set(PUBLIC_PAGE_KEYS)
    normalized = _enabled_privilege_keys(privileges)
    for privilege in normalized:
        allowed.update(PRIVILEGE_PAGE_MAP.get(privilege, set()))

    links = [PAGE_REGISTRY[key] for key in NAVIGATION_ORDER if key in allowed]
    if links:
        return links
    return [PAGE_REGISTRY[key] for key in NAVIGATION_ORDER if key in PUBLIC_PAGE_KEYS]


def _normalize_route(route: str) -> str:
    route = route or "/"
    if not route.startswith("/"):
        route = f"/{route}"
    if route != "/" and route.endswith("/"):
        route = route.rstrip("/")
    return route


def page_key_for_route(route: str) -> Optional[str]:
    return PATH_TO_PAGE_KEY.get(_normalize_route(route))


def accessible_page_keys(privileges: PrivilegeMapping | None) -> Set[str]:
    if _is_debug_mode():
        return set(NAVIGATION_ORDER)
    return {link.key for link in resolve_nav_links(privileges)}


def user_can_access_page(privileges: PrivilegeMapping | None, page_key: str) -> bool:
    if page_key not in PAGE_REGISTRY:
        return True
    if _is_debug_mode():
        return True
    return page_key in accessible_page_keys(privileges)


def default_page_path(privileges: PrivilegeMapping | None) -> str:
    links = resolve_nav_links(privileges)
    return links[0].path


def _is_debug_mode() -> bool:
    value = os.getenv(DEBUG_PRIVILEGES_ENV, "")
    return value.strip().lower() in _DEBUG_TRUE_VALUES
