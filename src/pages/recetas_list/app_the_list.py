from __future__ import annotations

import html
import json
import logging
import re
import time
import unicodedata
from pathlib import Path
from typing import Dict, List, Sequence
from urllib.parse import quote

import gradio as gr

from src.bucket_identity_store import get_user_preferences
from src.login_logic import get_user
from src.page_timing import timed_page_load
from src.pages.header import render_header, with_light_mode_head
from src.pages.recetas_list.core_the_list import (
    TAG_FILTER_ALL_OPTION,
    _build_tag_filter_choices,
    _build_tag_filter_update,
    _choice_values,
    _fetch_all_people,
    _filter_people_for_tag_selection,
    _normalize_selection,
    _normalize_tag,
    _parse_tag_query_values,
    _query_param,
    _read_recipe_payload,
    _render_tag_chips,
    _slugify,
    _write_recipe_payload,
)

logger = logging.getLogger(__name__)
timing_logger = logging.getLogger("uvicorn.error")

ASSETS_DIR = Path(__file__).resolve().parent
CSS_PATH = ASSETS_DIR / "css" / "the_list_page.css"
TAG_FILTER_JS_PATH = ASSETS_DIR / "js" / "the_list_tag_filter.js"
VERIFIED_ONLY_BUTTON_LABEL = "Solo"
VIEW_MODE_ICON = "icon"
VIEW_MODE_LIST = "list"
VIEW_MODE_ICON_LABEL = "Iconos"
VIEW_MODE_LIST_LABEL = "Lista"
ICON_MODE_BATCH_SIZE = 40
SEARCH_TOKEN_RE = re.compile(r"[a-z0-9]+")
THERMOMIX_FILTER_VALUE = "thermomix"
REDUCED_CATEGORY_VALUES = (
    "Thermomix",
    "Mamá",
    "Primer plato",
    "Carne",
    "Pescado",
    "Pasta",
    "Abolla",
    "Abuela",
    "Aperitivo",
    "Bebida",
    "Salsas",
)


def _log_timing(event_name: str, start: float, **fields: object) -> None:
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    if fields:
        field_text = " ".join(f"{key}={value}" for key, value in fields.items())
        timing_logger.info("recetas.page.timing event=%s ms=%.2f %s", event_name, elapsed_ms, field_text)
        return
    timing_logger.info("recetas.page.timing event=%s ms=%.2f", event_name, elapsed_ms)


def _read_asset(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.warning("Missing Recetas asset at %s", path)
        return ""


def _load_css() -> str:
    return _read_asset(CSS_PATH)


def _load_tag_filter_js() -> str:
    script = _read_asset(TAG_FILTER_JS_PATH)
    if not script:
        return ""
    return f"<script>\n{script}\n</script>"


def _normalize_view_mode(value: object) -> str:
    normalized = str(value or "").strip().lower()
    if normalized == VIEW_MODE_LIST:
        return VIEW_MODE_LIST
    return VIEW_MODE_ICON


def _normalize_icon_visible_count(value: object) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = ICON_MODE_BATCH_SIZE
    return max(ICON_MODE_BATCH_SIZE, parsed)


def _render_cards_with_incremental_icon_mode(
    recipes: Sequence[Dict[str, object]],
    *,
    view_mode: object,
    icon_visible_count: object,
    reduced_categories: object = False,
) -> tuple[str, int]:
    normalized_view_mode = _normalize_view_mode(view_mode)
    if normalized_view_mode == VIEW_MODE_LIST:
        return (
            _render_cards(recipes, view_mode=normalized_view_mode, reduced_categories=reduced_categories),
            _normalize_icon_visible_count(icon_visible_count),
        )

    total_count = len(recipes)
    visible_count = min(total_count, _normalize_icon_visible_count(icon_visible_count))
    visible_rows = list(recipes[:visible_count])
    cards_html = _render_cards(
        visible_rows,
        view_mode=normalized_view_mode,
        reduced_categories=reduced_categories,
    )
    if visible_count < total_count:
        cards_html += (
            "<div class='recipe-icon-load-sentinel' "
            "data-recipes-icon-sentinel='1' "
            f"data-rendered-count='{visible_count}' "
            f"data-total-count='{total_count}'>"
            f"Mostrando {visible_count} de {total_count} recetas. Desplázate para cargar más."
            "</div>"
        )
    return cards_html, visible_count


def _compact_values_text(values: object, *, empty_label: str) -> str:
    if isinstance(values, (list, tuple, set)):
        normalized_values = [str(value or "").strip() for value in values if str(value or "").strip()]
    else:
        normalized_values = []
    if not normalized_values:
        return html.escape(empty_label)
    return html.escape(", ".join(normalized_values))


def _render_cards(
    recipes: Sequence[Dict[str, object]],
    view_mode: object = VIEW_MODE_ICON,
    reduced_categories: object = False,
) -> str:
    if not recipes:
        return '<div class="people-empty">Aún no hay recetas disponibles.</div>'

    normalized_view_mode = _normalize_view_mode(view_mode)
    cards: List[str] = []
    for row in recipes:
        name = html.escape(str(row.get("name") or "Receta"))
        slug = str(row.get("slug") or "")
        href = f"/receta/?slug={quote(slug, safe='-')}"
        is_verified = bool(row.get("verified"))
        verified_class = "is-verified" if is_verified else "is-unverified"
        verified_list_class = " recipe-card__verified--list" if normalized_view_mode == VIEW_MODE_LIST else ""
        verified_label = "Receta verificada" if is_verified else "Receta sin verificar"
        safe_slug = html.escape(slug, quote=True)
        verified_badge = (
            f'<span class="recipe-card__verified{verified_list_class} {verified_class}" '
            f'role="button" aria-label="{verified_label}" title="{verified_label}" '
            f'data-slug="{safe_slug}" data-state="{"true" if is_verified else "false"}" tabindex="0"></span>'
        )

        card_color = html.escape(str(row.get("card_image") or "rgb(118, 161, 146)"), quote=True)
        card_image_file = str(row.get("card_image_file") or "").strip()
        card_image_src = html.escape(card_image_file, quote=True) if card_image_file else ""

        tags_raw = row.get("tags", [])
        tag_values = [_normalize_tag(str(tag)) for tag in tags_raw if str(tag).strip()]
        display_tags = _display_tags_for_preferences(tags_raw, reduced_categories)
        tags_markup = _render_tag_chips(display_tags, empty_label="sin-etiquetas")
        tags_list_text = _compact_values_text(display_tags, empty_label="sin etiquetas")
        tags_json_attr = html.escape(json.dumps(tag_values, ensure_ascii=True), quote=True)

        if normalized_view_mode == VIEW_MODE_LIST:
            cards.append(
                f"""
                <a class="recipe-list-row" href="{href}" data-tags-json="{tags_json_attr}">
                  <span class="recipe-list-row__cell recipe-list-row__cell--name">{name}</span>
                  <span class="recipe-list-row__cell recipe-list-row__cell--tags">{tags_list_text}</span>
                  <span class="recipe-list-row__verify">{verified_badge}</span>
                </a>
                """.strip()
            )
            continue

        image_wrap_class = "person-card__image-wrap"
        media_markup = "<div class='recipe-card__swatch' aria-hidden='true'></div>"
        if card_image_src:
            image_wrap_class += " person-card__image-wrap--has-image"
            media_markup = (
                f"<img class='recipe-card__image' src='{card_image_src}' alt='{name}' "
                "loading='lazy' decoding='async' fetchpriority='low'/>"
            )

        cards.append(
            f"""
            <a class="person-card" href="{href}" data-tags-json="{tags_json_attr}">
              <div class="{image_wrap_class}" style="--recipe-card-color: {card_color};">
                {media_markup}
                {verified_badge}
              </div>
              <div class="person-card__content">
                <h3 class="person-card__title">{name}</h3>
                <div class="recipe-card__chip-group">
                  <span class="recipe-card__chip-label">Etiquetas</span>
                  <div class="person-card__tags">{tags_markup}</div>
                </div>
              </div>
            </a>
            """.strip()
        )

    grid_classes = "people-grid"
    if normalized_view_mode == VIEW_MODE_LIST:
        return (
            '<div class="recipe-list-table" data-view-mode="list">'
            '<div class="recipe-list-table__header" aria-hidden="true">'
            '<span class="recipe-list-table__head recipe-list-table__head--name">Receta</span>'
            '<span class="recipe-list-table__head">Etiquetas</span>'
            '<span class="recipe-list-table__head recipe-list-table__head--verify">Verificado</span>'
            "</div>"
            f'<div class="recipe-list-table__body">{"".join(cards)}</div>'
            "</div>"
        )
    return f'<div class="{grid_classes}" data-view-mode="{normalized_view_mode}">{"".join(cards)}</div>'


def _resolve_next_filter_selection(
    current_selection: Sequence[object] | None,
    previous_selection: Sequence[object] | None,
    choices: Sequence[object],
    *,
    all_option: str,
) -> tuple[gr.update, List[str]]:
    choice_values = _choice_values(choices)
    all_key = _normalize_tag(all_option)
    allowed_values = [value for value in choice_values if _normalize_tag(value) != all_key]

    current_norm = {_normalize_tag(value) for value in _normalize_selection(current_selection)}
    previous_norm = {_normalize_tag(value) for value in _normalize_selection(previous_selection)}
    current_filtered = [value for value in allowed_values if _normalize_tag(value) in current_norm]
    previous_filtered = [value for value in allowed_values if _normalize_tag(value) in previous_norm]

    current_has_all = all_key in current_norm
    previous_has_all = all_key in previous_norm

    next_selection = current_filtered
    dropdown_update = gr.update()

    if current_has_all and not current_filtered:
        next_selection = [all_option, *allowed_values] if allowed_values else [all_option]
        dropdown_update = gr.update(value=next_selection)
    elif current_has_all and not previous_has_all:
        if (not current_filtered) or (current_filtered == previous_filtered):
            next_selection = [all_option, *allowed_values]
            dropdown_update = gr.update(value=next_selection)
        elif len(current_filtered) < len(allowed_values):
            next_selection = current_filtered
        else:
            next_selection = [all_option, *allowed_values]
    elif (not current_has_all) and previous_has_all and len(current_filtered) == len(allowed_values):
        next_selection = []
        dropdown_update = gr.update(value=next_selection)
    elif current_has_all and previous_has_all and len(current_filtered) < len(previous_filtered):
        next_selection = current_filtered
    elif current_has_all and len(current_filtered) == len(allowed_values):
        next_selection = [all_option, *allowed_values]
    elif len(current_filtered) == len(allowed_values) and allowed_values:
        next_selection = [all_option, *allowed_values]
        dropdown_update = gr.update(value=next_selection)

    next_has_all = any(_normalize_tag(value) == all_key for value in next_selection)
    if current_has_all and not next_has_all:
        dropdown_update = gr.update(value=next_selection)

    return dropdown_update, next_selection


def _normalize_search_text(value: object) -> str:
    raw_text = str(value or "").strip().lower()
    if not raw_text:
        return ""
    normalized = unicodedata.normalize("NFKD", raw_text)
    return "".join(char for char in normalized if not unicodedata.combining(char))


def _normalize_reduced_category_key(value: object) -> str:
    return _normalize_search_text(value)


def _reduced_category_keys() -> set[str]:
    return {_normalize_reduced_category_key(value) for value in REDUCED_CATEGORY_VALUES}


def _display_tags_for_preferences(tags: object, reduced_categories: object) -> List[str]:
    if isinstance(tags, (list, tuple, set)):
        normalized_tags = [str(tag or "").strip() for tag in tags if str(tag or "").strip()]
    else:
        normalized_tags = []
    if not _is_truthy(reduced_categories):
        return normalized_tags

    allowed_keys = _reduced_category_keys()
    return [
        tag
        for tag in normalized_tags
        if _normalize_reduced_category_key(tag) in allowed_keys
    ]


def _search_tokens(value: object) -> List[str]:
    normalized_text = _normalize_search_text(value)
    if not normalized_text:
        return []
    return [match.group(0) for match in SEARCH_TOKEN_RE.finditer(normalized_text)]


def _normalize_search_values(values: object) -> List[str]:
    if isinstance(values, str):
        raw_values = [chunk.strip() for chunk in values.split(",")]
    elif isinstance(values, (list, tuple, set)):
        raw_values = [str(value or "").strip() for value in values]
    else:
        return []

    normalized_values: List[str] = []
    for raw_value in raw_values:
        normalized = _normalize_search_text(raw_value)
        if normalized:
            normalized_values.append(normalized)
    return normalized_values


def _row_search_values(row: Dict[str, object]) -> List[str]:
    values: List[str] = [
        _normalize_search_text(row.get("name")),
        _normalize_search_text(row.get("slug")),
    ]
    values.extend(_normalize_search_values(row.get("tags", [])))
    return [value for value in values if value]


def _filter_people_for_search_query(
    recipes: Sequence[Dict[str, object]],
    search_query: object,
) -> List[Dict[str, object]]:
    query_tokens = _search_tokens(search_query)
    if not query_tokens:
        return list(recipes)

    filtered_rows: List[Dict[str, object]] = []
    for row in recipes:
        row_values = _row_search_values(row)
        if not row_values:
            continue

        row_tokens: set[str] = set()
        for value in row_values:
            row_tokens.update(match.group(0) for match in SEARCH_TOKEN_RE.finditer(value))
        if not row_tokens:
            continue

        if all(
            any(row_token.startswith(query_token) for row_token in row_tokens)
            for query_token in query_tokens
        ):
            filtered_rows.append(row)
    return filtered_rows


def _filter_people_for_verified_selection(
    recipes: Sequence[Dict[str, object]],
    only_verified: object,
) -> List[Dict[str, object]]:
    if not _is_truthy(only_verified):
        return list(recipes)
    return [row for row in recipes if bool(row.get("verified"))]


def _apply_recipe_filters(
    recipes: Sequence[Dict[str, object]],
    tag_selection: Sequence[object] | None,
    search_query: object = "",
    only_verified: object = True,
    show_thermomix: object = True,
) -> List[Dict[str, object]]:
    visible_rows = _filter_recipes_for_thermomix_visibility(recipes, show_thermomix)
    tag_filtered_rows = _filter_people_for_tag_selection(visible_rows, tag_selection)
    search_filtered_rows = _filter_people_for_search_query(tag_filtered_rows, search_query)
    return _filter_people_for_verified_selection(search_filtered_rows, only_verified)


def _is_truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "si", "sí"}


def _recipe_has_thermomix(row: Dict[str, object]) -> bool:
    values: List[object] = []
    raw_tags = row.get("tags", [])
    if isinstance(raw_tags, (list, tuple, set)):
        values.extend(raw_tags)
    return any(_normalize_tag(str(value)) == THERMOMIX_FILTER_VALUE for value in values)


def _filter_recipes_for_thermomix_visibility(
    recipes: Sequence[Dict[str, object]],
    show_thermomix: object,
) -> List[Dict[str, object]]:
    if _is_truthy(show_thermomix):
        return list(recipes)
    return [row for row in recipes if not _recipe_has_thermomix(row)]


def _build_reduced_tag_filter_choices(show_thermomix: object) -> List[tuple[str, str]]:
    choices: List[tuple[str, str]] = [(TAG_FILTER_ALL_OPTION, TAG_FILTER_ALL_OPTION)]
    for label in REDUCED_CATEGORY_VALUES:
        value = _normalize_reduced_category_key(label)
        if value == THERMOMIX_FILTER_VALUE and not _is_truthy(show_thermomix):
            continue
        choices.append((label, value))
    return choices


def _build_tag_filter_choices_for_preferences(
    recipes: Sequence[Dict[str, object]],
    reduced_categories: object,
    show_thermomix: object,
) -> List[tuple[str, str]]:
    if _is_truthy(reduced_categories):
        return _build_reduced_tag_filter_choices(show_thermomix)
    return _build_tag_filter_choices(recipes)


def _build_tag_filter_update_for_preferences(
    recipes: Sequence[Dict[str, object]],
    selected_values: Sequence[object] | None,
    *,
    default_to_all: bool,
    reduced_categories: object,
    show_thermomix: object,
) -> tuple[gr.update, List[tuple[str, str]], List[str]]:
    choices = _build_tag_filter_choices_for_preferences(recipes, reduced_categories, show_thermomix)
    _, resolved_selection = _resolve_next_filter_selection(
        selected_values,
        selected_values,
        choices,
        all_option=TAG_FILTER_ALL_OPTION,
    )
    if not selected_values:
        allowed_values = [
            value
            for value in _choice_values(choices)
            if _normalize_tag(value) != _normalize_tag(TAG_FILTER_ALL_OPTION)
        ]
        resolved_selection = [TAG_FILTER_ALL_OPTION, *allowed_values] if default_to_all and allowed_values else []
    return (
        gr.update(choices=choices, value=resolved_selection, interactive=True),
        choices,
        resolved_selection,
    )


def _render_cards_for_current_filters(
    current_tag_selection: Sequence[object] | None,
    search_query: object,
    only_verified: object,
    reduced_categories: object,
    show_thermomix: object,
    view_mode: object,
) -> tuple[gr.update, int]:
    recipes = _fetch_all_people()
    filtered_rows = _apply_recipe_filters(
        recipes,
        current_tag_selection,
        search_query,
        only_verified=only_verified,
        show_thermomix=show_thermomix,
    )
    cards_html, resolved_icon_visible_count = _render_cards_with_incremental_icon_mode(
        filtered_rows,
        view_mode=view_mode,
        icon_visible_count=ICON_MODE_BATCH_SIZE,
        reduced_categories=reduced_categories,
    )
    return gr.update(value=cards_html, visible=True), resolved_icon_visible_count


def _render_cards_for_current_filters_with_icon_count(
    current_tag_selection: Sequence[object] | None,
    search_query: object,
    only_verified: object,
    reduced_categories: object,
    show_thermomix: object,
    view_mode: object,
    icon_visible_count: object,
) -> tuple[gr.update, int]:
    recipes = _fetch_all_people()
    filtered_rows = _apply_recipe_filters(
        recipes,
        current_tag_selection,
        search_query,
        only_verified=only_verified,
        show_thermomix=show_thermomix,
    )
    cards_html, resolved_icon_visible_count = _render_cards_with_incremental_icon_mode(
        filtered_rows,
        view_mode=view_mode,
        icon_visible_count=icon_visible_count,
        reduced_categories=reduced_categories,
    )
    return gr.update(value=cards_html, visible=True), resolved_icon_visible_count


def _verified_only_button_update(only_verified: object) -> gr.update:
    enabled = _is_truthy(only_verified)
    return gr.update(
        value=VERIFIED_ONLY_BUTTON_LABEL,
        variant="primary" if enabled else "secondary",
    )


def _view_mode_button_update(current_mode: object, *, target_mode: str, label: str) -> gr.update:
    normalized_mode = _normalize_view_mode(current_mode)
    return gr.update(
        value=label,
        variant="primary" if normalized_mode == target_mode else "secondary",
    )


def _view_mode_button_updates(current_mode: object) -> tuple[gr.update, gr.update]:
    normalized_mode = _normalize_view_mode(current_mode)
    return (
        _view_mode_button_update(
            normalized_mode,
            target_mode=VIEW_MODE_ICON,
            label=VIEW_MODE_ICON_LABEL,
        ),
        _view_mode_button_update(
            normalized_mode,
            target_mode=VIEW_MODE_LIST,
            label=VIEW_MODE_LIST_LABEL,
        ),
    )


def _set_recipe_view_mode(
    next_view_mode: str,
    current_tag_selection: Sequence[object] | None,
    search_query: str,
    current_only_verified: object,
    current_reduced_categories: object,
    current_show_thermomix: object,
    current_icon_visible_count: object,
):
    total_start = time.perf_counter()
    normalized_view_mode = _normalize_view_mode(next_view_mode)
    requested_icon_count = (
        ICON_MODE_BATCH_SIZE if normalized_view_mode == VIEW_MODE_ICON else current_icon_visible_count
    )
    cards_update, next_icon_visible_count = _render_cards_for_current_filters_with_icon_count(
        current_tag_selection=current_tag_selection,
        search_query=search_query,
        only_verified=current_only_verified,
        reduced_categories=current_reduced_categories,
        show_thermomix=current_show_thermomix,
        view_mode=normalized_view_mode,
        icon_visible_count=requested_icon_count,
    )
    icon_button_update, list_button_update = _view_mode_button_updates(normalized_view_mode)
    _log_timing(
        "set_recipe_view_mode.total",
        total_start,
        view_mode=normalized_view_mode,
    )
    return normalized_view_mode, icon_button_update, list_button_update, next_icon_visible_count, cards_update


def _switch_to_icon_view(
    current_tag_selection: Sequence[object] | None,
    search_query: str,
    current_only_verified: object,
    current_reduced_categories: object,
    current_show_thermomix: object,
    current_icon_visible_count: object,
):
    return _set_recipe_view_mode(
        VIEW_MODE_ICON,
        current_tag_selection=current_tag_selection,
        search_query=search_query,
        current_only_verified=current_only_verified,
        current_reduced_categories=current_reduced_categories,
        current_show_thermomix=current_show_thermomix,
        current_icon_visible_count=current_icon_visible_count,
    )


def _switch_to_list_view(
    current_tag_selection: Sequence[object] | None,
    search_query: str,
    current_only_verified: object,
    current_reduced_categories: object,
    current_show_thermomix: object,
    current_icon_visible_count: object,
):
    return _set_recipe_view_mode(
        VIEW_MODE_LIST,
        current_tag_selection=current_tag_selection,
        search_query=search_query,
        current_only_verified=current_only_verified,
        current_reduced_categories=current_reduced_categories,
        current_show_thermomix=current_show_thermomix,
        current_icon_visible_count=current_icon_visible_count,
    )


def _toggle_verified_only_filter(
    current_only_verified: object,
    current_tag_selection: Sequence[object] | None,
    search_query: str,
    current_reduced_categories: object,
    current_show_thermomix: object,
    current_view_mode: object,
    current_icon_visible_count: object,
):
    total_start = time.perf_counter()
    next_only_verified = not _is_truthy(current_only_verified)
    cards_update, next_icon_visible_count = _render_cards_for_current_filters_with_icon_count(
        current_tag_selection=current_tag_selection,
        search_query=search_query,
        only_verified=next_only_verified,
        reduced_categories=current_reduced_categories,
        show_thermomix=current_show_thermomix,
        view_mode=current_view_mode,
        icon_visible_count=ICON_MODE_BATCH_SIZE,
    )
    button_update = _verified_only_button_update(next_only_verified)
    _log_timing(
        "toggle_verified_only_filter.total",
        total_start,
        only_verified=next_only_verified,
    )
    return next_only_verified, button_update, next_icon_visible_count, cards_update


def _toggle_recipe_verified_from_list(
    payload_json: str,
    current_tag_selection: Sequence[object] | None,
    search_query: str,
    current_only_verified: object,
    current_reduced_categories: object,
    current_show_thermomix: object,
    current_view_mode: object,
    current_icon_visible_count: object,
):
    total_start = time.perf_counter()
    try:
        payload = json.loads(payload_json or "{}")
    except json.JSONDecodeError:
        payload = {}

    raw_slug = str(payload.get("slug") or "").strip()
    slug = _slugify(raw_slug) if raw_slug else ""
    next_state = _is_truthy(payload.get("nextState"))
    has_state = "nextState" in payload
    if slug and has_state:
        recipe_payload = _read_recipe_payload(slug)
        if recipe_payload is not None:
            recipe_payload["Verified"] = bool(next_state)
            _write_recipe_payload(slug, recipe_payload)

    cards_update, next_icon_visible_count = _render_cards_for_current_filters_with_icon_count(
        current_tag_selection=current_tag_selection,
        search_query=search_query,
        only_verified=current_only_verified,
        reduced_categories=current_reduced_categories,
        show_thermomix=current_show_thermomix,
        view_mode=current_view_mode,
        icon_visible_count=current_icon_visible_count,
    )
    _log_timing(
        "toggle_recipe_verified_from_list.total",
        total_start,
        slug=slug or "<empty>",
        has_state=has_state,
    )
    return next_icon_visible_count, cards_update


def _update_people_cards_by_filters(
    current_tag_selection: Sequence[object] | None,
    previous_tag_selection: Sequence[object] | None,
    search_query: str,
    previous_search_query: str,
    current_only_verified: object,
    current_reduced_categories: object,
    current_show_thermomix: object,
    current_view_mode: object,
):
    total_start = time.perf_counter()

    recipes = _fetch_all_people()
    visible_recipes = _filter_recipes_for_thermomix_visibility(recipes, current_show_thermomix)
    tag_choices = _build_tag_filter_choices_for_preferences(
        visible_recipes,
        current_reduced_categories,
        current_show_thermomix,
    )

    tag_dropdown_update, next_tag_selection = _resolve_next_filter_selection(
        current_tag_selection,
        previous_tag_selection,
        tag_choices,
        all_option=TAG_FILTER_ALL_OPTION,
    )

    filtered_rows = _apply_recipe_filters(
        recipes,
        next_tag_selection,
        search_query,
        only_verified=current_only_verified,
        show_thermomix=current_show_thermomix,
    )
    current_search_query = str(search_query or "")
    search_cleared = bool(str(previous_search_query or "").strip()) and not current_search_query.strip()
    requested_icon_visible_count = ICON_MODE_BATCH_SIZE
    if search_cleared and _normalize_view_mode(current_view_mode) == VIEW_MODE_ICON:
        requested_icon_visible_count = max(ICON_MODE_BATCH_SIZE, len(filtered_rows))
    cards_html, next_icon_visible_count = _render_cards_with_incremental_icon_mode(
        filtered_rows,
        view_mode=current_view_mode,
        icon_visible_count=requested_icon_visible_count,
        reduced_categories=current_reduced_categories,
    )
    cards_update = gr.update(value=cards_html, visible=True)

    _log_timing(
        "update_filters.total",
        total_start,
        selected_tags=len(next_tag_selection),
        only_verified=_is_truthy(current_only_verified),
        reduced_categories=_is_truthy(current_reduced_categories),
        show_thermomix=_is_truthy(current_show_thermomix),
        search_chars=len(str(search_query or "").strip()),
        filtered_rows=len(filtered_rows),
    )
    return (
        tag_dropdown_update,
        next_tag_selection,
        next_icon_visible_count,
        cards_update,
        current_search_query,
    )


def _load_more_icon_recipes(
    current_icon_visible_count: object,
    current_tag_selection: Sequence[object] | None,
    search_query: str,
    current_only_verified: object,
    current_reduced_categories: object,
    current_show_thermomix: object,
    current_view_mode: object,
):
    total_start = time.perf_counter()
    normalized_view_mode = _normalize_view_mode(current_view_mode)
    if normalized_view_mode != VIEW_MODE_ICON:
        return _normalize_icon_visible_count(current_icon_visible_count), gr.update()

    next_requested_visible_count = _normalize_icon_visible_count(current_icon_visible_count) + ICON_MODE_BATCH_SIZE
    cards_update, next_icon_visible_count = _render_cards_for_current_filters_with_icon_count(
        current_tag_selection=current_tag_selection,
        search_query=search_query,
        only_verified=current_only_verified,
        reduced_categories=current_reduced_categories,
        show_thermomix=current_show_thermomix,
        view_mode=normalized_view_mode,
        icon_visible_count=next_requested_visible_count,
    )
    _log_timing(
        "load_more_icon_recipes.total",
        total_start,
        next_visible=next_icon_visible_count,
    )
    return next_icon_visible_count, cards_update


def _header_the_list(request: gr.Request):
    return render_header(path="/recetas", request=request)


def _load_the_list_page(request: gr.Request):
    total_start = time.perf_counter()
    try:
        recipes = _fetch_all_people()
        selected_tags = _parse_tag_query_values(_query_param(request, "tag"))
        search_query = _query_param(request, "q") or _query_param(request, "search")
        raw_view_mode = _query_param(request, "view")
        raw_only_verified = _query_param(request, "solo")

        default_view_mode = VIEW_MODE_ICON
        default_only_verified = True
        default_reduced_categories = True
        default_show_thermomix = True
        user = get_user(request, refresh_privileges=False) or {}
        user_email = str((user or {}).get("email") or "").strip().lower()
        if user_email:
            preferences = get_user_preferences(user_email)
            default_view_mode = _normalize_view_mode(preferences.get("recetas_view_mode"))
            default_only_verified = _is_truthy(preferences.get("recetas_only_verified"))
            default_reduced_categories = _is_truthy(preferences.get("recetas_reduced_categories", True))
            default_show_thermomix = _is_truthy(preferences.get("recetas_show_thermomix", True))

        view_mode = _normalize_view_mode(raw_view_mode or default_view_mode)
        only_verified = _is_truthy(raw_only_verified) if raw_only_verified else default_only_verified
        visible_recipes = _filter_recipes_for_thermomix_visibility(recipes, default_show_thermomix)
        tag_filter_update, _tag_filter_choices, tag_filter_selection = _build_tag_filter_update_for_preferences(
            visible_recipes,
            selected_tags,
            default_to_all=False,
            reduced_categories=default_reduced_categories,
            show_thermomix=default_show_thermomix,
        )
        filtered_rows = _apply_recipe_filters(
            recipes,
            tag_filter_selection,
            search_query,
            only_verified=only_verified,
            show_thermomix=default_show_thermomix,
        )
        cards_html, icon_visible_count = _render_cards_with_incremental_icon_mode(
            filtered_rows,
            view_mode=view_mode,
            icon_visible_count=ICON_MODE_BATCH_SIZE,
            reduced_categories=default_reduced_categories,
        )
        icon_view_button_update, list_view_button_update = _view_mode_button_updates(view_mode)

        _log_timing(
            "load_recetas.total",
            total_start,
            recipes=len(recipes),
            selected_tags=len(tag_filter_selection),
            only_verified=only_verified,
            reduced_categories=default_reduced_categories,
            show_thermomix=default_show_thermomix,
            search_chars=len(str(search_query or "").strip()),
            filtered=len(filtered_rows),
        )
        return (
            "<h2>Recetas</h2>",
            gr.update(visible=True),
            gr.update(value=search_query),
            _verified_only_button_update(only_verified),
            icon_view_button_update,
            list_view_button_update,
            only_verified,
            default_reduced_categories,
            default_show_thermomix,
            view_mode,
            tag_filter_update,
            tag_filter_selection,
            icon_visible_count,
            gr.update(value=cards_html, visible=True),
            str(search_query or ""),
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to load Recetas page: %s", exc)
        return (
            "<h2>Recetas</h2>",
            gr.update(visible=False),
            gr.update(value=""),
            _verified_only_button_update(True),
            _view_mode_button_update(
                VIEW_MODE_ICON,
                target_mode=VIEW_MODE_ICON,
                label=VIEW_MODE_ICON_LABEL,
            ),
            _view_mode_button_update(
                VIEW_MODE_ICON,
                target_mode=VIEW_MODE_LIST,
                label=VIEW_MODE_LIST_LABEL,
            ),
            True,
            True,
            True,
            VIEW_MODE_ICON,
            gr.update(choices=[(TAG_FILTER_ALL_OPTION, TAG_FILTER_ALL_OPTION)], value=[], interactive=True),
            [],
            ICON_MODE_BATCH_SIZE,
            gr.update(value='<div class="people-empty">No se pudieron cargar las recetas.</div>', visible=True),
            "",
        )


def make_the_list_app() -> gr.Blocks:
    stylesheet = _load_css()
    tag_filter_js = _load_tag_filter_js()
    with gr.Blocks(
        title="Recetas",
        css=stylesheet or None,
        head=with_light_mode_head(tag_filter_js),
    ) as app:
        hdr = gr.HTML()

        with gr.Column(elem_id="people-shell"):
            with gr.Row(elem_id="people-title-row"):
                title_md = gr.HTML("<h2>Recetas</h2>", elem_id="people-title")
                search_box = gr.Textbox(
                    label="Buscar recetas",
                    placeholder="Buscar recetas",
                    value="",
                    lines=1,
                    interactive=True,
                    show_label=False,
                    container=False,
                    elem_id="people-search",
                )
                with gr.Row(elem_id="people-filter-row", visible=True) as tag_filter_row:
                    tag_filter = gr.Dropdown(
                        label="Filtrar por etiquetas",
                        choices=[(TAG_FILTER_ALL_OPTION, TAG_FILTER_ALL_OPTION)],
                        value=[],
                        multiselect=True,
                        allow_custom_value=False,
                        interactive=True,
                        show_label=False,
                        container=False,
                        elem_id="people-tag-filter",
                    )
                    verified_only_toggle = gr.Button(
                        VERIFIED_ONLY_BUTTON_LABEL,
                        variant="primary",
                        elem_id="people-verified-only-toggle",
                        scale=0,
                        min_width=98,
                    )
                    view_icon_toggle = gr.Button(
                        VIEW_MODE_ICON_LABEL,
                        variant="primary",
                        elem_id="people-view-icon-toggle",
                        scale=0,
                        min_width=42,
                    )
                    view_list_toggle = gr.Button(
                        VIEW_MODE_LIST_LABEL,
                        variant="secondary",
                        elem_id="people-view-list-toggle",
                        scale=0,
                        min_width=42,
                    )
                    gr.Button(
                        "+",
                        variant="secondary",
                        elem_id="the-list-create-profile-trigger",
                        scale=0,
                        min_width=40,
                    )

            tag_filter_selection_state = gr.State([])
            verified_only_state = gr.State(True)
            reduced_categories_state = gr.State(True)
            show_thermomix_state = gr.State(True)
            view_mode_state = gr.State(VIEW_MODE_ICON)
            icon_visible_count_state = gr.State(ICON_MODE_BATCH_SIZE)
            search_query_state = gr.State("")
            cards_html = gr.HTML(elem_id="people-cards")
            verify_payload = gr.Textbox(
                value="",
                show_label=False,
                interactive=False,
                visible=False,
                elem_id="recipe-verify-payload",
            )
            verify_trigger = gr.Button(
                "_toggle_verified",
                visible=False,
                elem_id="recipe-verify-trigger",
            )
            icon_load_more_trigger = gr.Button(
                "_load_more_icon_recipes",
                visible=False,
                elem_id="recipe-icon-load-more-trigger",
            )

        app.load(timed_page_load("/recetas", _header_the_list), outputs=[hdr])
        app.load(
            timed_page_load("/recetas", _load_the_list_page),
            outputs=[
                title_md,
                tag_filter_row,
                search_box,
                verified_only_toggle,
                view_icon_toggle,
                view_list_toggle,
                verified_only_state,
                reduced_categories_state,
                show_thermomix_state,
                view_mode_state,
                tag_filter,
                tag_filter_selection_state,
                icon_visible_count_state,
                cards_html,
                search_query_state,
            ],
        )

        filter_update_handler = timed_page_load(
            "/recetas",
            _update_people_cards_by_filters,
            label="update_receta_cards_by_filters",
        )

        tag_filter.input(
            filter_update_handler,
            inputs=[
                tag_filter,
                tag_filter_selection_state,
                search_box,
                search_query_state,
                verified_only_state,
                reduced_categories_state,
                show_thermomix_state,
                view_mode_state,
            ],
            outputs=[
                tag_filter,
                tag_filter_selection_state,
                icon_visible_count_state,
                cards_html,
                search_query_state,
            ],
            show_progress=False,
        )
        search_box.input(
            filter_update_handler,
            inputs=[
                tag_filter,
                tag_filter_selection_state,
                search_box,
                search_query_state,
                verified_only_state,
                reduced_categories_state,
                show_thermomix_state,
                view_mode_state,
            ],
            outputs=[
                tag_filter,
                tag_filter_selection_state,
                icon_visible_count_state,
                cards_html,
                search_query_state,
            ],
            show_progress=False,
            trigger_mode="always_last",
        )
        verified_only_toggle.click(
            timed_page_load(
                "/recetas",
                _toggle_verified_only_filter,
                label="toggle_verified_only_filter",
            ),
            inputs=[
                verified_only_state,
                tag_filter,
                search_box,
                reduced_categories_state,
                show_thermomix_state,
                view_mode_state,
                icon_visible_count_state,
            ],
            outputs=[verified_only_state, verified_only_toggle, icon_visible_count_state, cards_html],
            show_progress=False,
        )
        view_icon_toggle.click(
            timed_page_load(
                "/recetas",
                _switch_to_icon_view,
                label="switch_to_icon_view",
            ),
            inputs=[
                tag_filter,
                search_box,
                verified_only_state,
                reduced_categories_state,
                show_thermomix_state,
                icon_visible_count_state,
            ],
            outputs=[view_mode_state, view_icon_toggle, view_list_toggle, icon_visible_count_state, cards_html],
            show_progress=False,
        )
        view_list_toggle.click(
            timed_page_load(
                "/recetas",
                _switch_to_list_view,
                label="switch_to_list_view",
            ),
            inputs=[
                tag_filter,
                search_box,
                verified_only_state,
                reduced_categories_state,
                show_thermomix_state,
                icon_visible_count_state,
            ],
            outputs=[view_mode_state, view_icon_toggle, view_list_toggle, icon_visible_count_state, cards_html],
            show_progress=False,
        )
        verify_trigger.click(
            timed_page_load(
                "/recetas",
                _toggle_recipe_verified_from_list,
                label="toggle_recipe_verified_from_list",
            ),
            inputs=[
                verify_payload,
                tag_filter,
                search_box,
                verified_only_state,
                reduced_categories_state,
                show_thermomix_state,
                view_mode_state,
                icon_visible_count_state,
            ],
            outputs=[icon_visible_count_state, cards_html],
            show_progress=False,
        )
        icon_load_more_trigger.click(
            timed_page_load(
                "/recetas",
                _load_more_icon_recipes,
                label="load_more_icon_recipes",
            ),
            inputs=[
                icon_visible_count_state,
                tag_filter,
                search_box,
                verified_only_state,
                reduced_categories_state,
                show_thermomix_state,
                view_mode_state,
            ],
            outputs=[icon_visible_count_state, cards_html],
            show_progress=False,
        )

    return app
