from __future__ import annotations

import base64
import binascii
import html
import logging
import re
import time
from pathlib import Path
from typing import Dict, List, Sequence

import gradio as gr

from src.page_timing import timed_page_load
from src.pages.header import render_header, with_light_mode_head
from src.pages.recetas_list.core_the_list import (
    DEFAULT_CARD_COLOR,
    _fetch_all_people,
    _fetch_recipe_by_slug,
    _query_param,
    _read_recipe_payload,
    _render_recipe_markdown,
    _render_recipe_hero,
    _slugify,
    _write_recipe_payload,
)

logger = logging.getLogger(__name__)

ASSETS_DIR = Path(__file__).resolve().parent
CSS_PATH = ASSETS_DIR / "css" / "people_display_page.css"
EDITOR_JS_PATH = ASSETS_DIR / "js" / "people_editor.js"
PROJECT_ROOT = Path(__file__).resolve().parents[3]
RECIPE_IMAGES_DIR = PROJECT_ROOT / "images" / "recipes"

EDIT_TOGGLE_BUTTON_LABEL = " "
CARD_EDITOR_HELP = "Edit card, ingredients and recipe, then submit."

MAX_IMAGE_BYTES = 8 * 1024 * 1024
ALLOWED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"}
ALLOWED_IMAGE_MIME_TYPES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/svg+xml": ".svg",
}
DATA_URL_IMAGE_RE = re.compile(r"^data:(image/[a-z0-9.+-]+);base64,([a-z0-9+/=\s]+)$", re.IGNORECASE)


def _read_asset(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.warning("Missing recipe display asset at %s", path)
        return ""


def _load_css() -> str:
    return _read_asset(CSS_PATH)


def _load_editor_js() -> str:
    script = _read_asset(EDITOR_JS_PATH)
    if not script:
        return ""
    return f"<script>\n{script}\n</script>"


def _header_people_display(request: gr.Request):
    return render_header(path="/recetas", request=request)


def _render_recipe_selection_prompt() -> str:
    return (
        "<section class='person-detail-card person-detail-card--missing'>"
        "<div class='person-detail-card__body'>"
        "<h2>Select a recipe</h2>"
        "<p>Open a card from Recetas to view and edit the recipe.</p>"
        "</div></section>"
    )


def _render_missing_recipe(slug: str) -> str:
    safe_slug = html.escape(slug or "unknown")
    return (
        "<section class='person-detail-card person-detail-card--missing'>"
        "<div class='person-detail-card__body'>"
        "<h2>Recipe not found</h2>"
        f"<p>No recipe matched slug <code>{safe_slug}</code>.</p>"
        "</div></section>"
    )


def _normalize_values(values: Sequence[object] | None) -> List[str]:
    parsed: List[str] = []
    seen: set[str] = set()
    for value in values or []:
        normalized = str(value or "").strip().lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        parsed.append(normalized)
    return parsed


def _collect_choices(field_name: str) -> List[str]:
    values: set[str] = set()
    for recipe in _fetch_all_people():
        for raw in recipe.get(field_name, []):
            normalized = str(raw or "").strip().lower()
            if normalized:
                values.add(normalized)
    return sorted(values)


def _ingredients_to_text(recipe: Dict[str, object]) -> str:
    lines: List[str] = []
    for ingredient_name, amount in recipe.get("ingredients", []):
        name_text = str(ingredient_name or "").strip()
        amount_text = str(amount or "").strip()
        if name_text and amount_text:
            lines.append(f"{amount_text} | {name_text}")
        elif name_text:
            lines.append(name_text)
        elif amount_text:
            lines.append(f"{amount_text} |")
    return "\n".join(lines)


def _steps_to_text(recipe: Dict[str, object]) -> str:
    return "\n".join(str(step or "").strip() for step in recipe.get("steps", []) if str(step or "").strip())


def _parse_ingredients_input(raw_value: str) -> Dict[str, str]:
    ingredients: Dict[str, str] = {}
    for line in str(raw_value or "").splitlines():
        value = line.strip()
        if not value:
            continue

        amount = ""
        ingredient = value
        if "|" in value:
            left, right = value.split("|", 1)
            amount = left.strip()
            ingredient = right.strip()
        elif ":" in value:
            left, right = value.split(":", 1)
            amount = left.strip()
            ingredient = right.strip()

        if not ingredient:
            continue
        ingredients[ingredient] = amount
    return ingredients


def _parse_steps_input(raw_value: str) -> List[str]:
    parsed: List[str] = []
    for line in str(raw_value or "").splitlines():
        value = line.strip()
        if not value:
            continue
        value = re.sub(r"^\d+[.)]\s*", "", value).strip()
        if value:
            parsed.append(value)
    return parsed


def _parse_inline_values(raw_value: str) -> List[str]:
    parsed: List[str] = []
    seen: set[str] = set()
    for chunk in re.split(r"[\n,]+", str(raw_value or "")):
        normalized = str(chunk or "").strip().lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        parsed.append(normalized)
    return parsed


def _extract_upload_path(uploaded_image: object) -> str:
    if not uploaded_image:
        return ""
    if isinstance(uploaded_image, Path):
        return str(uploaded_image)
    if isinstance(uploaded_image, str):
        return uploaded_image
    if isinstance(uploaded_image, dict):
        return str(uploaded_image.get("path") or uploaded_image.get("name") or "")
    if isinstance(uploaded_image, (list, tuple)):
        for item in uploaded_image:
            candidate = _extract_upload_path(item)
            if candidate:
                return candidate
    return ""


def _extract_upload_image_bytes(uploaded_image: object) -> tuple[bytes | None, str, str]:
    upload_path = _extract_upload_path(uploaded_image)
    if not upload_path:
        return None, "", ""

    source = Path(upload_path.strip())
    if not source.is_file():
        return None, "", "Uploaded image could not be read."

    extension = source.suffix.lower()
    if extension not in ALLOWED_IMAGE_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_IMAGE_EXTENSIONS))
        return None, "", f"Unsupported image format. Allowed: {allowed}"

    image_bytes = source.read_bytes()
    if not image_bytes:
        return None, "", "Uploaded image is empty."
    if len(image_bytes) > MAX_IMAGE_BYTES:
        return None, "", f"Image exceeds {MAX_IMAGE_BYTES // (1024 * 1024)} MB limit."

    return image_bytes, extension, ""


def _decode_data_url_image(image_data_url: str) -> tuple[bytes | None, str, str]:
    raw_payload = str(image_data_url or "").strip()
    if not raw_payload:
        return None, "", ""

    match = DATA_URL_IMAGE_RE.match(raw_payload)
    if not match:
        return None, "", "Cropped image payload is invalid."

    mime_type = str(match.group(1) or "").strip().lower()
    extension = ALLOWED_IMAGE_MIME_TYPES.get(mime_type)
    if not extension:
        allowed = ", ".join(sorted(ALLOWED_IMAGE_MIME_TYPES))
        return None, "", f"Unsupported cropped image type `{mime_type}`. Allowed: {allowed}"

    base64_payload = re.sub(r"\s+", "", str(match.group(2) or ""))
    try:
        image_bytes = base64.b64decode(base64_payload, validate=True)
    except (binascii.Error, ValueError):
        return None, "", "Cropped image payload could not be decoded."

    if not image_bytes:
        return None, "", "Cropped image payload is empty."
    if len(image_bytes) > MAX_IMAGE_BYTES:
        return None, "", f"Image exceeds {MAX_IMAGE_BYTES // (1024 * 1024)} MB limit."

    return image_bytes, extension, ""


def _save_recipe_image(slug: str, image_bytes: bytes, extension: str) -> str:
    normalized_extension = str(extension or "").strip().lower() or ".png"
    if normalized_extension not in ALLOWED_IMAGE_EXTENSIONS:
        normalized_extension = ".png"

    RECIPE_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{_slugify(slug)}-{int(time.time())}{normalized_extension}"
    output_path = RECIPE_IMAGES_DIR / filename
    output_path.write_bytes(image_bytes)
    return f"/images/recipes/{filename}"


def _build_edit_form(recipe: Dict[str, object]) -> Dict[str, str]:
    tags = _normalize_values(recipe.get("tags", []))
    tools = _normalize_values(recipe.get("tools", []))
    return {
        "slug": str(recipe.get("slug") or "").strip(),
        "name": str(recipe.get("name") or "").strip(),
        "bucket": "",
        "tags_text": ", ".join(tags),
        "tools_text": ", ".join(tools),
        "total_time": str(recipe.get("total_time") or "").strip(),
        "persons": str(recipe.get("persons") or "").strip(),
        "ingredients": _ingredients_to_text(recipe),
        "steps": _steps_to_text(recipe),
        "image_route": str(recipe.get("card_image_file") or "").strip(),
    }


def _empty_page_state(title_html: str, detail_html: str, page_message: str = ""):
    return (
        title_html,
        gr.update(value=EDIT_TOGGLE_BUTTON_LABEL, visible=False),
        gr.update(value=page_message, visible=bool(page_message)),
        gr.update(value=detail_html, visible=True),
        gr.update(value="", visible=False),
        gr.update(visible=False),
        CARD_EDITOR_HELP,
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        gr.update(value="", visible=False),
        gr.update(value=None),
    )


def _recipe_page_state(
    recipe: Dict[str, object],
    *,
    editing: bool,
    page_message: str = "",
    card_message: str = "",
):
    form = _build_edit_form(recipe)
    recipe_for_render = dict(recipe)
    recipe_for_render["tag_catalog"] = _collect_choices("tags")
    recipe_for_render["tool_catalog"] = _collect_choices("tools")

    return (
        f"<h2>{html.escape(str(recipe.get('name') or 'Recipe'))}</h2>",
        gr.update(value=EDIT_TOGGLE_BUTTON_LABEL, visible=True),
        gr.update(value=page_message, visible=bool(page_message)),
        gr.update(value=_render_recipe_hero(recipe_for_render), visible=True),
        gr.update(value=_render_recipe_markdown(recipe), visible=not editing),
        gr.update(visible=editing),
        CARD_EDITOR_HELP,
        form["name"],
        form["bucket"],
        form["tags_text"],
        form["tools_text"],
        form["total_time"],
        form["persons"],
        "",
        form["ingredients"],
        form["steps"],
        form["slug"],
        form["name"],
        form["bucket"],
        form["tags_text"],
        form["tools_text"],
        form["total_time"],
        form["persons"],
        form["image_route"],
        gr.update(value=card_message, visible=bool(card_message)),
        gr.update(value=None),
    )


def _state_from_slug(
    slug: str,
    *,
    editing: bool,
    page_message: str = "",
    card_message: str = "",
):
    recipe = _fetch_recipe_by_slug(slug)
    if recipe is None:
        return _empty_page_state(
            "<h2>Recipe not found</h2>",
            _render_missing_recipe(slug),
            page_message=page_message or "❌ Recipe not found.",
        )
    return _recipe_page_state(
        recipe,
        editing=editing,
        page_message=page_message,
        card_message=card_message,
    )


def _load_people_display_page(request: gr.Request):
    try:
        slug = _query_param(request, "slug").lower()
        if not slug:
            return _empty_page_state("<h2>Recetas</h2>", _render_recipe_selection_prompt())

        recipe = _fetch_recipe_by_slug(slug)
        if recipe is None:
            return _empty_page_state("<h2>Recipe not found</h2>", _render_missing_recipe(slug))

        return _recipe_page_state(recipe, editing=False)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to load recipe display page: %s", exc)
        return _empty_page_state(
            "<h2>Recetas</h2>",
            _render_missing_recipe("load-error"),
            page_message="❌ Could not load recipe.",
        )


def _open_edit_mode(current_slug: str):
    raw_slug = str(current_slug or "").strip()
    if not raw_slug:
        return _empty_page_state("<h2>Recetas</h2>", _render_recipe_selection_prompt())
    normalized_slug = _slugify(raw_slug)
    return _state_from_slug(normalized_slug, editing=True)


def _cancel_edit_mode(current_slug: str):
    raw_slug = str(current_slug or "").strip()
    if not raw_slug:
        return _empty_page_state("<h2>Recetas</h2>", _render_recipe_selection_prompt())
    normalized_slug = _slugify(raw_slug)
    return _state_from_slug(normalized_slug, editing=False)


def _save_recipe_edits(
    current_slug: str,
    card_proposal_name: str,
    _card_proposal_bucket: str,
    card_proposal_tags: str,
    card_proposal_tools: str,
    card_proposal_total_time: str,
    card_proposal_persons: str,
    card_proposal_image: object,
    card_proposal_image_data: str,
    edit_ingredients: str,
    edit_steps: str,
    current_image_route: str,
):
    raw_slug = str(current_slug or "").strip()
    if not raw_slug:
        return _empty_page_state(
            "<h2>Recetas</h2>",
            _render_recipe_selection_prompt(),
            page_message="❌ Select a recipe first.",
        )
    normalized_slug = _slugify(raw_slug)

    existing_payload = _read_recipe_payload(normalized_slug)
    if existing_payload is None:
        return _state_from_slug(
            normalized_slug,
            editing=True,
            card_message="❌ Could not save: recipe JSON file not found.",
        )

    clean_name = str(card_proposal_name or "").strip() or str(existing_payload.get("Name") or "Recipe")
    clean_total_time = str(card_proposal_total_time or "").strip() or "Not specified"
    clean_persons = str(card_proposal_persons or "").strip() or "Not specified"
    # Card color is legacy fallback only; image is the authoritative card visual.
    clean_card_color = str(existing_payload.get("card image") or DEFAULT_CARD_COLOR).strip() or DEFAULT_CARD_COLOR
    clean_tags = _parse_inline_values(card_proposal_tags)
    clean_tools = _parse_inline_values(card_proposal_tools)
    ingredients = _parse_ingredients_input(edit_ingredients)
    steps = _parse_steps_input(edit_steps)

    image_route = str(current_image_route or existing_payload.get("card image file") or "").strip()

    cropped_bytes, cropped_ext, cropped_error = _decode_data_url_image(card_proposal_image_data)
    if cropped_error:
        return _state_from_slug(normalized_slug, editing=True, card_message=f"❌ {cropped_error}")

    upload_bytes, upload_ext, upload_error = _extract_upload_image_bytes(card_proposal_image)
    if upload_error:
        return _state_from_slug(normalized_slug, editing=True, card_message=f"❌ {upload_error}")

    image_bytes = cropped_bytes or upload_bytes
    image_extension = cropped_ext or upload_ext
    if image_bytes:
        try:
            image_route = _save_recipe_image(normalized_slug, image_bytes, image_extension)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to save recipe image: %s", exc)
            return _state_from_slug(normalized_slug, editing=True, card_message="❌ Could not save image.")

    next_payload: Dict[str, object] = dict(existing_payload)
    next_payload["Name"] = clean_name
    next_payload["Ingredients"] = ingredients
    next_payload["Steps"] = steps
    next_payload["Total time"] = clean_total_time
    next_payload["Nºpersonas"] = clean_persons
    next_payload["card image"] = clean_card_color
    next_payload["Tags"] = clean_tags
    next_payload["Tools"] = clean_tools
    if image_route:
        next_payload["card image file"] = image_route
    else:
        next_payload.pop("card image file", None)

    if not _write_recipe_payload(normalized_slug, next_payload):
        return _state_from_slug(
            normalized_slug,
            editing=True,
            card_message="❌ Could not save recipe JSON file.",
        )

    return _state_from_slug(
        normalized_slug,
        editing=False,
        page_message="✅ Recipe updated locally.",
    )


def make_people_display_app() -> gr.Blocks:
    stylesheet = _load_css()
    editor_js = _load_editor_js()
    with gr.Blocks(
        title="Receta",
        css=stylesheet or None,
        head=with_light_mode_head(editor_js),
    ) as app:
        hdr = gr.HTML()
        image_route_state = gr.State("")

        with gr.Column(elem_id="people-shell"):
            with gr.Row(elem_id="people-title-row"):
                title_md = gr.HTML("<h2>Recetas</h2>", elem_id="people-title")
                edit_btn = gr.Button(
                    EDIT_TOGGLE_BUTTON_LABEL,
                    visible=False,
                    variant="secondary",
                    elem_id="the-list-card-edit-btn",
                )

            page_status = gr.Markdown(value="", visible=False, elem_id="recipe-page-status")
            detail_html = gr.HTML(visible=False, elem_id="person-detail-hero")
            detail_markdown = gr.HTML(visible=False, elem_id="person-detail-markdown")
            gr.HTML(value="", visible=False, elem_id="the-list-review-link")

            with gr.Column(visible=False, elem_id="the-list-card-proposal-shell") as card_proposal_shell:
                card_proposal_help = gr.Markdown(CARD_EDITOR_HELP, elem_id="the-list-card-proposal-help")

                with gr.Row(elem_id="the-list-card-proposal-grid"):
                    card_proposal_name = gr.Textbox(label="Card name", elem_id="the-list-card-proposal-name")
                    card_proposal_bucket = gr.Textbox(label="Card title", elem_id="the-list-card-proposal-bucket")

                card_proposal_tags = gr.Textbox(
                    label="Card tags",
                    lines=2,
                    placeholder="Comma-separated tags",
                    elem_id="the-list-card-proposal-tags",
                )
                card_proposal_tools = gr.Textbox(
                    value="",
                    visible=False,
                    interactive=True,
                    elem_id="the-list-card-proposal-tools",
                )
                card_proposal_total_time = gr.Textbox(
                    value="",
                    visible=False,
                    interactive=True,
                    elem_id="the-list-card-proposal-total-time",
                )
                card_proposal_persons = gr.Textbox(
                    value="",
                    visible=False,
                    interactive=True,
                    elem_id="the-list-card-proposal-persons",
                )

                with gr.Row(elem_id="the-list-card-image-row"):
                    gr.Markdown("**Card image**")
                    card_proposal_image = gr.UploadButton(
                        "+",
                        file_types=["image"],
                        file_count="single",
                        elem_id="the-list-card-image-plus-btn",
                        scale=0,
                        min_width=40,
                    )

                card_proposal_image_data = gr.Textbox(
                    value="",
                    visible=False,
                    interactive=True,
                    elem_id="the-list-card-image-data",
                )

                with gr.Row(elem_id="recipe-editor-grid"):
                    edit_ingredients_input = gr.Textbox(
                        label="Ingredientes",
                        show_label=False,
                        lines=14,
                        placeholder="3 ripe | avocado",
                        elem_id="recipe-editor-ingredients-input",
                    )
                    edit_steps_input = gr.Textbox(
                        label="Preparación",
                        show_label=False,
                        lines=14,
                        placeholder="Mash avocados in a bowl until mostly smooth.",
                        elem_id="recipe-editor-steps-input",
                    )

                with gr.Row(elem_id="the-list-card-proposal-actions"):
                    submit_btn = gr.Button("Submit", variant="primary")
                    cancel_btn = gr.Button("Cancel", variant="secondary")

                card_proposal_status = gr.Markdown(value="", visible=False, elem_id="the-list-card-proposal-status")

            current_slug = gr.Textbox(value="", visible=False, interactive=False, elem_id="the-list-current-slug")
            current_name = gr.Textbox(value="", visible=False, interactive=False, elem_id="the-list-current-name")
            current_bucket = gr.Textbox(
                value="",
                visible=False,
                interactive=False,
                elem_id="the-list-current-bucket",
            )
            current_tags = gr.Textbox(value="", visible=False, interactive=False, elem_id="the-list-current-tags")
            current_tools = gr.Textbox(value="", visible=False, interactive=False, elem_id="the-list-current-tools")
            current_total_time = gr.Textbox(
                value="",
                visible=False,
                interactive=False,
                elem_id="the-list-current-total-time",
            )
            current_persons = gr.Textbox(
                value="",
                visible=False,
                interactive=False,
                elem_id="the-list-current-persons",
            )

        page_state_outputs = [
            title_md,
            edit_btn,
            page_status,
            detail_html,
            detail_markdown,
            card_proposal_shell,
            card_proposal_help,
            card_proposal_name,
            card_proposal_bucket,
            card_proposal_tags,
            card_proposal_tools,
            card_proposal_total_time,
            card_proposal_persons,
            card_proposal_image_data,
            edit_ingredients_input,
            edit_steps_input,
            current_slug,
            current_name,
            current_bucket,
            current_tags,
            current_tools,
            current_total_time,
            current_persons,
            image_route_state,
            card_proposal_status,
            card_proposal_image,
        ]

        app.load(timed_page_load("/receta", _header_people_display), outputs=[hdr])
        app.load(
            timed_page_load("/receta", _load_people_display_page),
            outputs=page_state_outputs,
        )

        edit_btn.click(
            timed_page_load("/receta", _open_edit_mode, label="open_recipe_edit_mode"),
            inputs=[current_slug],
            outputs=page_state_outputs,
            show_progress=False,
        )

        cancel_btn.click(
            timed_page_load("/receta", _cancel_edit_mode, label="cancel_recipe_edit_mode"),
            inputs=[current_slug],
            outputs=page_state_outputs,
            show_progress=False,
        )

        submit_btn.click(
            timed_page_load("/receta", _save_recipe_edits, label="save_recipe_edits"),
            inputs=[
                current_slug,
                card_proposal_name,
                card_proposal_bucket,
                card_proposal_tags,
                card_proposal_tools,
                card_proposal_total_time,
                card_proposal_persons,
                card_proposal_image,
                card_proposal_image_data,
                edit_ingredients_input,
                edit_steps_input,
                image_route_state,
            ],
            outputs=page_state_outputs,
            show_progress=False,
        )

    return app
