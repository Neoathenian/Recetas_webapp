from __future__ import annotations

import html
import json
import logging
import time
from pathlib import Path
from typing import Dict, List, Sequence
from urllib.parse import quote

import gradio as gr

from src.page_timing import timed_page_load
from src.pages.header import render_header, with_light_mode_head
from src.pages.recetas_list.core_the_list import (
    TAG_FILTER_ALL_OPTION,
    TOOL_FILTER_ALL_OPTION,
    _build_tag_filter_choices,
    _build_tag_filter_update,
    _build_tool_filter_choices,
    _build_tool_filter_update,
    _choice_values,
    _fetch_all_people,
    _filter_people_for_tag_selection,
    _filter_people_for_tool_selection,
    _normalize_selection,
    _normalize_tag,
    _parse_tag_query_values,
    _query_param,
    _render_tag_chips,
)

logger = logging.getLogger(__name__)
timing_logger = logging.getLogger("uvicorn.error")

ASSETS_DIR = Path(__file__).resolve().parent
CSS_PATH = ASSETS_DIR / "css" / "the_list_page.css"
TAG_FILTER_JS_PATH = ASSETS_DIR / "js" / "the_list_tag_filter.js"


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


def _render_cards(recipes: Sequence[Dict[str, object]]) -> str:
    if not recipes:
        return '<div class="people-empty">No recipes are available yet.</div>'

    cards: List[str] = []
    for row in recipes:
        name = html.escape(str(row.get("name") or "Recipe"))
        slug = str(row.get("slug") or "")
        href = f"/receta/?slug={quote(slug, safe='-')}"

        card_color = html.escape(str(row.get("card_image") or "rgb(118, 161, 146)"), quote=True)
        card_image_file = str(row.get("card_image_file") or "").strip()
        card_image_src = html.escape(card_image_file, quote=True) if card_image_file else ""

        tag_values = [_normalize_tag(str(tag)) for tag in row.get("tags", []) if str(tag).strip()]
        tags_markup = _render_tag_chips(row.get("tags", []), empty_label="no-tags")
        tools_markup = _render_tag_chips(row.get("tools", []), empty_label="no-tools")
        tags_json_attr = html.escape(json.dumps(tag_values, ensure_ascii=True), quote=True)
        image_wrap_class = "person-card__image-wrap"
        media_markup = "<div class='recipe-card__swatch' aria-hidden='true'></div>"
        if card_image_src:
            image_wrap_class += " person-card__image-wrap--has-image"
            media_markup = f"<img class='recipe-card__image' src='{card_image_src}' alt='{name}' loading='lazy'/>"

        cards.append(
            f"""
            <a class="person-card" href="{href}" data-tags-json="{tags_json_attr}">
              <div class="{image_wrap_class}" style="--recipe-card-color: {card_color};">
                {media_markup}
              </div>
              <div class="person-card__content">
                <h3 class="person-card__title">{name}</h3>
                <div class="recipe-card__chip-group">
                  <span class="recipe-card__chip-label">Tags</span>
                  <div class="person-card__tags">{tags_markup}</div>
                </div>
                <div class="recipe-card__chip-group">
                  <span class="recipe-card__chip-label">Tools</span>
                  <div class="person-card__tags">{tools_markup}</div>
                </div>
              </div>
            </a>
            """.strip()
        )

    return f'<div class="people-grid">{"".join(cards)}</div>'


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


def _normalize_search_query(value: object) -> str:
    return str(value or "").strip().lower()


def _filter_people_for_search_query(
    recipes: Sequence[Dict[str, object]],
    search_query: object,
) -> List[Dict[str, object]]:
    normalized_query = _normalize_search_query(search_query)
    if not normalized_query:
        return list(recipes)

    filtered_rows: List[Dict[str, object]] = []
    for row in recipes:
        searchable_values: List[str] = [
            str(row.get("name") or "").strip().lower(),
            str(row.get("slug") or "").strip().lower(),
        ]
        searchable_values.extend(str(tag or "").strip().lower() for tag in row.get("tags", []))
        searchable_values.extend(str(tool or "").strip().lower() for tool in row.get("tools", []))
        if any(normalized_query in value for value in searchable_values if value):
            filtered_rows.append(row)
    return filtered_rows


def _apply_recipe_filters(
    recipes: Sequence[Dict[str, object]],
    tag_selection: Sequence[object] | None,
    tool_selection: Sequence[object] | None,
    search_query: object = "",
) -> List[Dict[str, object]]:
    tag_filtered_rows = _filter_people_for_tag_selection(recipes, tag_selection)
    tool_filtered_rows = _filter_people_for_tool_selection(tag_filtered_rows, tool_selection)
    return _filter_people_for_search_query(tool_filtered_rows, search_query)


def _update_people_cards_by_filters(
    current_tag_selection: Sequence[object] | None,
    previous_tag_selection: Sequence[object] | None,
    current_tool_selection: Sequence[object] | None,
    previous_tool_selection: Sequence[object] | None,
    search_query: str,
):
    total_start = time.perf_counter()

    recipes = _fetch_all_people()
    tag_choices = _build_tag_filter_choices(recipes)
    tool_choices = _build_tool_filter_choices(recipes)

    tag_dropdown_update, next_tag_selection = _resolve_next_filter_selection(
        current_tag_selection,
        previous_tag_selection,
        tag_choices,
        all_option=TAG_FILTER_ALL_OPTION,
    )
    tool_dropdown_update, next_tool_selection = _resolve_next_filter_selection(
        current_tool_selection,
        previous_tool_selection,
        tool_choices,
        all_option=TOOL_FILTER_ALL_OPTION,
    )

    filtered_rows = _apply_recipe_filters(recipes, next_tag_selection, next_tool_selection, search_query)
    cards_update = gr.update(value=_render_cards(filtered_rows), visible=True)

    _log_timing(
        "update_filters.total",
        total_start,
        selected_tags=len(next_tag_selection),
        selected_tools=len(next_tool_selection),
        search_chars=len(str(search_query or "").strip()),
        filtered_rows=len(filtered_rows),
    )
    return (
        tag_dropdown_update,
        next_tag_selection,
        tool_dropdown_update,
        next_tool_selection,
        cards_update,
    )


def _header_the_list(request: gr.Request):
    return render_header(path="/recetas", request=request)


def _load_the_list_page(request: gr.Request):
    total_start = time.perf_counter()
    try:
        recipes = _fetch_all_people()
        selected_tags = _parse_tag_query_values(_query_param(request, "tag"))
        selected_tools = _parse_tag_query_values(_query_param(request, "tool"))
        search_query = _query_param(request, "q") or _query_param(request, "search")
        tag_filter_update, _tag_filter_choices, tag_filter_selection = _build_tag_filter_update(
            recipes,
            selected_tags,
            default_to_all=False,
        )
        tool_filter_update, _tool_filter_choices, tool_filter_selection = _build_tool_filter_update(
            recipes,
            selected_tools,
            default_to_all=False,
        )
        filtered_rows = _apply_recipe_filters(recipes, tag_filter_selection, tool_filter_selection, search_query)
        cards_html = _render_cards(filtered_rows)

        _log_timing(
            "load_recetas.total",
            total_start,
            recipes=len(recipes),
            selected_tags=len(tag_filter_selection),
            selected_tools=len(tool_filter_selection),
            search_chars=len(str(search_query or "").strip()),
            filtered=len(filtered_rows),
        )
        return (
            "<h2>Recetas</h2>",
            gr.update(visible=True),
            gr.update(value=search_query),
            tag_filter_update,
            tag_filter_selection,
            tool_filter_update,
            tool_filter_selection,
            gr.update(value=cards_html, visible=True),
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to load Recetas page: %s", exc)
        return (
            "<h2>Recetas</h2>",
            gr.update(visible=False),
            gr.update(value=""),
            gr.update(choices=[(TAG_FILTER_ALL_OPTION, TAG_FILTER_ALL_OPTION)], value=[], interactive=True),
            [],
            gr.update(choices=[(TOOL_FILTER_ALL_OPTION, TOOL_FILTER_ALL_OPTION)], value=[], interactive=True),
            [],
            gr.update(value='<div class="people-empty">Could not load recipes.</div>', visible=True),
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
                    label="Search recipes",
                    placeholder="Search recipes",
                    value="",
                    lines=1,
                    interactive=True,
                    show_label=False,
                    container=False,
                    elem_id="people-search",
                )
                with gr.Row(elem_id="people-filter-row", visible=True) as tag_filter_row:
                    tag_filter = gr.Dropdown(
                        label="Filter by tags",
                        choices=[(TAG_FILTER_ALL_OPTION, TAG_FILTER_ALL_OPTION)],
                        value=[],
                        multiselect=True,
                        allow_custom_value=False,
                        interactive=True,
                        show_label=False,
                        container=False,
                        elem_id="people-tag-filter",
                    )
                    tool_filter = gr.Dropdown(
                        label="Filter by tools",
                        choices=[(TOOL_FILTER_ALL_OPTION, TOOL_FILTER_ALL_OPTION)],
                        value=[],
                        multiselect=True,
                        allow_custom_value=False,
                        interactive=True,
                        show_label=False,
                        container=False,
                        elem_id="people-tool-filter",
                    )

            tag_filter_selection_state = gr.State([])
            tool_filter_selection_state = gr.State([])
            cards_html = gr.HTML(elem_id="people-cards")

        app.load(timed_page_load("/recetas", _header_the_list), outputs=[hdr])
        app.load(
            timed_page_load("/recetas", _load_the_list_page),
            outputs=[
                title_md,
                tag_filter_row,
                search_box,
                tag_filter,
                tag_filter_selection_state,
                tool_filter,
                tool_filter_selection_state,
                cards_html,
            ],
        )

        filter_update_handler = timed_page_load(
            "/recetas",
            _update_people_cards_by_filters,
            label="update_receta_cards_by_filters",
        )

        tag_filter.input(
            filter_update_handler,
            inputs=[tag_filter, tag_filter_selection_state, tool_filter, tool_filter_selection_state, search_box],
            outputs=[tag_filter, tag_filter_selection_state, tool_filter, tool_filter_selection_state, cards_html],
            show_progress=False,
        )
        tool_filter.input(
            filter_update_handler,
            inputs=[tag_filter, tag_filter_selection_state, tool_filter, tool_filter_selection_state, search_box],
            outputs=[tag_filter, tag_filter_selection_state, tool_filter, tool_filter_selection_state, cards_html],
            show_progress=False,
        )
        search_box.input(
            filter_update_handler,
            inputs=[tag_filter, tag_filter_selection_state, tool_filter, tool_filter_selection_state, search_box],
            outputs=[tag_filter, tag_filter_selection_state, tool_filter, tool_filter_selection_state, cards_html],
            show_progress=False,
        )

    return app
