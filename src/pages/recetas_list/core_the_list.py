from __future__ import annotations

import html
import json
import logging
import re
import time
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

import gradio as gr

TAG_FILTER_ALL_OPTION = "All"
DEFAULT_CARD_COLOR = "rgb(118, 161, 146)"
RECIPES_DIR = Path(__file__).resolve().parents[3] / "data" / "recipes"
RGB_RE = re.compile(r"^rgb\s*\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*\)$", re.IGNORECASE)
TRANSPARENT_PIXEL_DATA_URL = "data:image/gif;base64,R0lGODlhAQABAAAAACw="
RECIPE_ICON_TOTAL_TIME = (
    "<img class='recipe-meta-item__icon recipe-meta-item__icon--img' "
    "src='/images/clock.png' alt='' aria-hidden='true'/>"
)
RECIPE_ICON_PERSONS = (
    "<svg class='recipe-meta-item__icon' viewBox='0 0 24 24' fill='none' "
    "xmlns='http://www.w3.org/2000/svg' aria-hidden='true' focusable='false'>"
    "<circle cx='9' cy='9' r='2.2' stroke='currentColor' stroke-width='1.9'/>"
    "<circle cx='15.3' cy='9.5' r='1.9' stroke='currentColor' stroke-width='1.9'/>"
    "<path d='M4.8 17.2a4.4 4.4 0 0 1 8.8 0' stroke='currentColor' stroke-width='1.9' "
    "stroke-linecap='round'/>"
    "<path d='M12.5 16.6a3.6 3.6 0 0 1 5.7.6' stroke='currentColor' stroke-width='1.9' "
    "stroke-linecap='round'/>"
    "</svg>"
)

logger = logging.getLogger(__name__)
timing_logger = logging.getLogger("uvicorn.error")


def _log_timing(event_name: str, start: float, **fields: object) -> None:
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    if fields:
        field_text = " ".join(f"{key}={value}" for key, value in fields.items())
        timing_logger.info("recipes.timing event=%s ms=%.2f %s", event_name, elapsed_ms, field_text)
        return
    timing_logger.info("recipes.timing event=%s ms=%.2f", event_name, elapsed_ms)


def _slugify(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower())
    return normalized.strip("-") or "recipe"


def _recipe_path_for_slug(slug: str) -> Path:
    return RECIPES_DIR / f"{_slugify(slug)}.json"


def _read_recipe_payload(slug: str) -> Dict[str, object] | None:
    recipe_path = _recipe_path_for_slug(slug)
    try:
        payload = json.loads(recipe_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except json.JSONDecodeError as exc:
        logger.warning("Recipe payload `%s` has invalid JSON: %s", recipe_path.name, exc)
        return None

    if not isinstance(payload, dict):
        logger.warning("Recipe payload `%s` is not a JSON object", recipe_path.name)
        return None
    return payload


def _write_recipe_payload(slug: str, payload: Dict[str, object]) -> bool:
    recipe_path = _recipe_path_for_slug(slug)
    try:
        recipe_path.parent.mkdir(parents=True, exist_ok=True)
        recipe_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed writing recipe payload `%s`: %s", recipe_path, exc)
        return False


def _normalize_tag(value: str) -> str:
    return str(value or "").strip().lower()


def _normalize_selection(values: Sequence[object] | None) -> List[str]:
    normalized_values: List[str] = []
    seen: set[str] = set()
    for value in values or []:
        text = str(value or "").strip()
        if not text:
            continue
        normalized = _normalize_tag(text)
        if normalized in seen:
            continue
        seen.add(normalized)
        normalized_values.append(text)
    return normalized_values


def _choice_values(choices: Sequence[object]) -> List[str]:
    values: List[str] = []
    for choice in choices or []:
        if isinstance(choice, (tuple, list)) and len(choice) >= 2:
            raw_value = choice[1]
        else:
            raw_value = choice
        text = str(raw_value or "").strip()
        if text and text not in values:
            values.append(text)
    return values


def _query_param(request: gr.Request | None, key: str) -> str:
    if request is None:
        return ""
    request_obj = getattr(request, "request", request)
    query_params = getattr(request_obj, "query_params", None)
    if not query_params:
        return ""
    return str(query_params.get(key, "")).strip()


def _parse_tag_query_values(raw_query: str) -> List[str]:
    query = str(raw_query or "").strip()
    if not query:
        return []

    if query.startswith("["):
        try:
            parsed = json.loads(query)
        except json.JSONDecodeError:
            parsed = []
        if isinstance(parsed, list):
            return _normalize_selection(parsed)

    values = [part.strip() for part in query.split(",") if part.strip()]
    return _normalize_selection(values)


def _normalize_color(value: object) -> str:
    raw = str(value or "").strip()
    match = RGB_RE.match(raw)
    if not match:
        return DEFAULT_CARD_COLOR
    channels = [int(match.group(index)) for index in range(1, 4)]
    if any(channel < 0 or channel > 255 for channel in channels):
        return DEFAULT_CARD_COLOR
    return f"rgb({channels[0]}, {channels[1]}, {channels[2]})"


def _string_or_default(value: object, default: str) -> str:
    text_value = str(value or "").strip()
    return text_value or default


def _parse_list(value: object) -> List[str]:
    if isinstance(value, (list, tuple, set)):
        raw_values = [str(item or "").strip() for item in value]
    elif isinstance(value, str):
        raw_values = [chunk.strip() for chunk in value.split(",")]
    else:
        raw_values = []

    parsed: List[str] = []
    seen: set[str] = set()
    for raw_item in raw_values:
        normalized = _normalize_tag(raw_item)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        parsed.append(normalized)
    return parsed


def _parse_ingredients(value: object) -> List[Tuple[str, str]]:
    if not isinstance(value, dict):
        return []

    ingredients: List[Tuple[str, str]] = []
    for raw_name, raw_amount in value.items():
        ingredient_name = str(raw_name or "").strip()
        amount_value = str(raw_amount or "").strip()
        if not ingredient_name and not amount_value:
            continue
        ingredients.append((ingredient_name, amount_value))
    return ingredients


def _parse_steps(value: object) -> List[str]:
    if isinstance(value, (list, tuple)):
        raw_steps = [str(item or "").strip() for item in value]
    elif isinstance(value, str):
        raw_steps = [line.strip() for line in value.splitlines()]
    else:
        raw_steps = []

    return [step for step in raw_steps if step]


def _display_name_from_slug(slug: str) -> str:
    parts = [chunk for chunk in re.split(r"[-_]+", str(slug or "").strip()) if chunk]
    if not parts:
        return "Recipe"
    return " ".join(part.capitalize() for part in parts)


def _recipe_from_payload(payload: Dict[str, object], file_path: Path) -> Dict[str, object]:
    slug = _slugify(str(payload.get("slug") or file_path.stem))
    name = _string_or_default(payload.get("Name") or payload.get("name"), _display_name_from_slug(slug))

    ingredients = _parse_ingredients(payload.get("Ingredients") or payload.get("ingredients"))
    steps = _parse_steps(payload.get("Steps") or payload.get("steps"))

    preparation_time = _string_or_default(
        payload.get("Preparation time") or payload.get("preparation_time"),
        "Not specified",
    )
    total_time = _string_or_default(
        payload.get("Total time") or payload.get("total_time"),
        "Not specified",
    )
    persons = _string_or_default(
        payload.get("N\u00bapersonas") or payload.get("n_personas") or payload.get("persons"),
        "Not specified",
    )

    return {
        "slug": slug,
        "name": name,
        "ingredients": ingredients,
        "steps": steps,
        "preparation_time": preparation_time,
        "total_time": total_time,
        "persons": persons,
        "card_image": _normalize_color(payload.get("card image") or payload.get("card_image")),
        "card_image_file": str(payload.get("card image file") or payload.get("card_image_file") or "").strip(),
        "tags": _parse_list(payload.get("Tags") or payload.get("tags")),
        "tools": _parse_list(payload.get("Tools") or payload.get("tools")),
    }


def _load_recipe_from_file(file_path: Path) -> Dict[str, object] | None:
    try:
        payload = json.loads(file_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except json.JSONDecodeError as exc:
        logger.warning("Skipping recipe `%s`: invalid JSON (%s)", file_path.name, exc)
        return None

    if not isinstance(payload, dict):
        logger.warning("Skipping recipe `%s`: payload must be a JSON object", file_path.name)
        return None
    return _recipe_from_payload(payload, file_path)


def _fetch_all_people() -> List[Dict[str, object]]:
    total_start = time.perf_counter()
    recipes: List[Dict[str, object]] = []

    if not RECIPES_DIR.exists():
        _log_timing("fetch_all_recipes.empty_directory", total_start, path=str(RECIPES_DIR))
        return recipes

    for recipe_path in sorted(RECIPES_DIR.glob("*.json")):
        recipe = _load_recipe_from_file(recipe_path)
        if recipe is None:
            continue
        recipes.append(recipe)

    recipes.sort(key=lambda row: str(row.get("name") or "").lower())
    _log_timing("fetch_all_recipes.total", total_start, recipes=len(recipes))
    return recipes


def _fetch_recipe_by_slug(slug: str) -> Dict[str, object] | None:
    normalized_slug = _slugify(slug)
    for recipe in _fetch_all_people():
        if str(recipe.get("slug") or "") == normalized_slug:
            return recipe
    return None


def _build_tag_filter_choices(people: Sequence[Dict[str, object]]) -> List[Tuple[str, str]]:
    unique_tags: set[str] = set()
    for row in people:
        for raw_tag in row.get("tags", []):
            normalized_tag = _normalize_tag(str(raw_tag))
            if normalized_tag:
                unique_tags.add(normalized_tag)

    sorted_tags = sorted(unique_tags)
    choices: List[Tuple[str, str]] = [(TAG_FILTER_ALL_OPTION, TAG_FILTER_ALL_OPTION)]
    choices.extend((tag, tag) for tag in sorted_tags)
    return choices


def _resolve_tag_filter_selection(
    choices: Sequence[object],
    selected_values: Sequence[object] | None,
    *,
    default_to_all: bool,
) -> List[str]:
    all_values = _choice_values(choices)
    if not all_values:
        return []

    all_option_normalized = _normalize_tag(TAG_FILTER_ALL_OPTION)
    allowed_values = [value for value in all_values if _normalize_tag(value) != all_option_normalized]
    if not allowed_values:
        return []

    selected = _normalize_selection(selected_values)
    if not selected:
        return [TAG_FILTER_ALL_OPTION, *allowed_values] if default_to_all else []

    selected_normalized = {_normalize_tag(value) for value in selected}
    filtered_values = [value for value in allowed_values if _normalize_tag(value) in selected_normalized]
    has_all = all_option_normalized in selected_normalized

    if has_all:
        if not filtered_values:
            return [TAG_FILTER_ALL_OPTION, *allowed_values]
        if len(filtered_values) == len(allowed_values):
            return [TAG_FILTER_ALL_OPTION, *allowed_values]
        return filtered_values

    if not filtered_values:
        return [TAG_FILTER_ALL_OPTION, *allowed_values] if default_to_all else []

    if len(filtered_values) == len(allowed_values):
        return [TAG_FILTER_ALL_OPTION, *allowed_values]
    return filtered_values


def _build_tag_filter_update(
    people: Sequence[Dict[str, object]],
    selected_values: Sequence[object] | None = None,
    *,
    default_to_all: bool = True,
) -> tuple[gr.update, List[Tuple[str, str]], List[str]]:
    choices = _build_tag_filter_choices(people)
    resolved_selection = _resolve_tag_filter_selection(choices, selected_values, default_to_all=default_to_all)
    return (
        gr.update(choices=choices, value=resolved_selection, interactive=True),
        choices,
        resolved_selection,
    )


def _filter_people_for_tag_selection(
    people: Sequence[Dict[str, object]],
    selected_values: Sequence[object] | None,
) -> List[Dict[str, object]]:
    selected_normalized = {
        _normalize_tag(value)
        for value in _normalize_selection(selected_values)
        if _normalize_tag(value)
    }
    all_key = _normalize_tag(TAG_FILTER_ALL_OPTION)
    if all_key in selected_normalized:
        return list(people)

    selected_normalized.discard(all_key)
    if not selected_normalized:
        return list(people)

    filtered_rows: List[Dict[str, object]] = []
    for row in people:
        row_tags = {
            _normalize_tag(str(tag))
            for tag in row.get("tags", [])
            if _normalize_tag(str(tag))
        }
        if row_tags.intersection(selected_normalized):
            filtered_rows.append(row)
    return filtered_rows


def _render_tag_chips(tags: Sequence[str], *, empty_label: str = "no-tags") -> str:
    if not tags:
        return f'<span class="person-tag person-tag--muted">{html.escape(empty_label)}</span>'
    parts: List[str] = []
    for tag in tags:
        safe_tag = html.escape(str(tag))
        parts.append(f'<span class="person-tag">{safe_tag}</span>')
    return "".join(parts)


def _render_recipe_hero(recipe: Dict[str, object]) -> str:
    name = html.escape(str(recipe.get("name") or "Recipe"))
    card_color = html.escape(str(recipe.get("card_image") or DEFAULT_CARD_COLOR), quote=True)
    total_time = html.escape(str(recipe.get("total_time") or "Not specified"))
    persons = html.escape(str(recipe.get("persons") or "Not specified"))
    tags_markup = _render_tag_chips(recipe.get("tags", []), empty_label="no-tags")
    tools_markup = _render_tag_chips(recipe.get("tools", []), empty_label="no-tools")
    image_route = str(recipe.get("card_image_file") or "").strip()
    image_src = html.escape(image_route or TRANSPARENT_PIXEL_DATA_URL, quote=True)
    image_class = "recipe-card-color__image"
    media_classes = "person-detail-card__media recipe-card-color"
    swatch_markup = ""
    if not image_route:
        swatch_markup = "<div class='recipe-card-color__swatch' aria-hidden='true'></div>"
    else:
        media_classes += " recipe-card-color--has-image"

    tag_catalog_values = _parse_list(recipe.get("tag_catalog") or recipe.get("tags") or [])
    tag_catalog_json = html.escape(json.dumps(tag_catalog_values, ensure_ascii=True), quote=True)
    tool_catalog_values = _parse_list(recipe.get("tool_catalog") or recipe.get("tools") or [])
    tool_catalog_json = html.escape(json.dumps(tool_catalog_values, ensure_ascii=True), quote=True)

    return f"""
    <section class="person-detail-card recipe-detail-card">
      <div class="{media_classes}" style="--recipe-card-color: {card_color};">
        {swatch_markup}
        <img class="{image_class}" src="{image_src}" alt="{name}" loading="lazy"/>
      </div>
      <div class="person-detail-card__body">
        <h2 class="person-detail-card__title">{name}</h2>
        <p class="person-detail-card__bucket" hidden></p>
        <div class="recipe-meta-grid">
          <div class="recipe-meta-item">
            <div class="recipe-meta-item__icon-wrap">{RECIPE_ICON_TOTAL_TIME}</div>
            <div class="recipe-meta-item__content">
              <span>Total time</span>
              <strong class="recipe-meta-item__value recipe-meta-item__value--total-time">{total_time}</strong>
            </div>
          </div>
          <div class="recipe-meta-item">
            <div class="recipe-meta-item__icon-wrap">{RECIPE_ICON_PERSONS}</div>
            <div class="recipe-meta-item__content">
              <span>N\u00bapersonas</span>
              <strong class="recipe-meta-item__value recipe-meta-item__value--persons">{persons}</strong>
            </div>
          </div>
        </div>
        <div class="recipe-chip-section">
          <span class="recipe-chip-title">Tags</span>
          <div class="person-detail-card__tags" data-tag-catalog="{tag_catalog_json}" data-inline-field-id="the-list-card-proposal-tags">{tags_markup}</div>
        </div>
        <div class="recipe-chip-section">
          <span class="recipe-chip-title">Tools</span>
          <div class="person-detail-card__tags person-detail-card__tools" data-tag-catalog="{tool_catalog_json}" data-inline-field-id="the-list-card-proposal-tools">{tools_markup}</div>
        </div>
        <div id="person-detail-card-inline-actions-slot" class="person-detail-card__inline-actions-slot"></div>
      </div>
    </section>
    """


def _generate_recipe_markdown(recipe: Dict[str, object]) -> str:
    """
    Keep a plain markdown representation generated from JSON.
    """
    ingredients = recipe.get("ingredients", [])
    steps = recipe.get("steps", [])

    ingredient_lines: List[str] = []
    for ingredient_name, amount in ingredients:
        amount_text = str(amount or "").strip()
        name_text = str(ingredient_name or "").strip()
        if amount_text and name_text:
            ingredient_lines.append(f"- {amount_text} {name_text}".strip())
        elif name_text:
            ingredient_lines.append(f"- {name_text}")
        elif amount_text:
            ingredient_lines.append(f"- {amount_text}")

    step_lines: List[str] = []
    for index, step in enumerate(steps, start=1):
        step_lines.append(f"{index}. {str(step or '').strip()}".strip())

    total_time = str(recipe.get("total_time") or "Not specified").strip()
    persons = str(recipe.get("persons") or "Not specified").strip()
    tags = ", ".join(str(tag or "").strip() for tag in recipe.get("tags", [])) or "No tags"
    tools = ", ".join(str(tool or "").strip() for tool in recipe.get("tools", [])) or "No tools"
    ingredients_block = "\n".join(ingredient_lines) if ingredient_lines else "- No ingredients provided."
    steps_block = "\n".join(step_lines) if step_lines else "1. No preparation steps provided."

    return (
        "## Recipe Summary\n"
        f"- **Total time:** {total_time}\n"
        f"- **N\u00bapersonas:** {persons}\n"
        f"- **Tags:** {tags}\n"
        f"- **Tools:** {tools}\n\n"
        "## Ingredientes\n"
        f"{ingredients_block}\n\n"
        "## Preparación\n"
        f"{steps_block}\n"
    )


def _render_recipe_markdown(recipe: Dict[str, object]) -> str:
    """
    Render a Thermomix-style compiled layout from recipe JSON:
    ingredients isolated on the left and preparation on the right.
    """
    ingredients = recipe.get("ingredients", [])
    steps = recipe.get("steps", [])

    ingredient_items: List[str] = []
    for ingredient_name, amount in ingredients:
        amount_text = html.escape(str(amount or "").strip())
        name_text = html.escape(str(ingredient_name or "").strip())
        if amount_text and name_text:
            ingredient_items.append(
                "<li class='recipe-ingredients-list__item'>"
                f"<span class='recipe-ingredients-list__amount'>{amount_text}</span>"
                f"<span class='recipe-ingredients-list__name'>{name_text}</span>"
                "</li>"
            )
        elif name_text:
            ingredient_items.append(
                "<li class='recipe-ingredients-list__item'>"
                f"<span class='recipe-ingredients-list__name'>{name_text}</span>"
                "</li>"
            )
        elif amount_text:
            ingredient_items.append(
                "<li class='recipe-ingredients-list__item'>"
                f"<span class='recipe-ingredients-list__name'>{amount_text}</span>"
                "</li>"
            )

    if not ingredient_items:
        ingredient_items.append(
            "<li class='recipe-ingredients-list__item recipe-ingredients-list__item--empty'>No ingredients provided.</li>"
        )

    step_items: List[str] = []
    for index, step in enumerate(steps, start=1):
        step_text = html.escape(str(step or "").strip())
        if not step_text:
            continue
        step_items.append(
            "<li class='recipe-steps-list__item'>"
            f"<span class='recipe-steps-list__index'>{index}</span>"
            f"<span class='recipe-steps-list__text'>{step_text}</span>"
            "</li>"
        )

    if not step_items:
        step_items.append(
            "<li class='recipe-steps-list__item recipe-steps-list__item--empty'>"
            "<span class='recipe-steps-list__text'>No preparation steps provided.</span>"
            "</li>"
        )

    return (
        "<div class='recipe-markdown-layout'>"
        "<section class='recipe-markdown-panel recipe-markdown-panel--ingredients'>"
        "<h3>Ingredientes</h3>"
        f"<ul class='recipe-ingredients-list'>{''.join(ingredient_items)}</ul>"
        "</section>"
        "<section class='recipe-markdown-panel recipe-markdown-panel--preparation'>"
        "<h3>Preparación</h3>"
        f"<ol class='recipe-steps-list'>{''.join(step_items)}</ol>"
        "</section>"
        "</div>"
    )
