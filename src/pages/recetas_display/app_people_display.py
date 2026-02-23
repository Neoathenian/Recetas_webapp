from __future__ import annotations

import base64
import binascii
import html
import logging
import os
import re
import time
from pathlib import Path
from typing import Dict, List, Sequence

import gradio as gr

from src.gcs_storage import media_path, upload_bytes
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
RECIPE_IMAGES_PREFIX = (os.getenv("RECETAS_IMAGES_PREFIX") or "recipes/images").strip("/ ")
TRUE_VALUES = {"1", "true", "yes", "on"}
NEW_RECIPE_SENTINEL = "__new_recipe__"

EDIT_TOGGLE_BUTTON_LABEL = " "
CARD_EDITOR_HELP = "Edita la tarjeta, los ingredientes y la receta, y luego guarda."

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


def _is_truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value or "").strip().lower() in TRUE_VALUES


def _bool_state(value: object) -> str:
    return "true" if _is_truthy(value) else "false"


def _is_create_request(request: gr.Request | None) -> bool:
    return _is_truthy(_query_param(request, "create") or _query_param(request, "new"))


def _render_recipe_selection_prompt() -> str:
    return (
        "<section class='person-detail-card person-detail-card--missing'>"
        "<div class='person-detail-card__body'>"
        "<h2>Selecciona una receta</h2>"
        "<p>Abre una tarjeta de Recetas para ver y editar la receta.</p>"
        "</div></section>"
    )


def _render_missing_recipe(slug: str) -> str:
    safe_slug = html.escape(slug or "unknown")
    return (
        "<section class='person-detail-card person-detail-card--missing'>"
        "<div class='person-detail-card__body'>"
        "<h2>Receta no encontrada</h2>"
        f"<p>No se encontró ninguna receta con el slug <code>{safe_slug}</code>.</p>"
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
        return None, "", "No se pudo leer la imagen subida."

    extension = source.suffix.lower()
    if extension not in ALLOWED_IMAGE_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_IMAGE_EXTENSIONS))
        return None, "", f"Formato de imagen no compatible. Permitidos: {allowed}"

    image_bytes = source.read_bytes()
    if not image_bytes:
        return None, "", "La imagen subida está vacía."
    if len(image_bytes) > MAX_IMAGE_BYTES:
        return None, "", f"La imagen supera el límite de {MAX_IMAGE_BYTES // (1024 * 1024)} MB."

    return image_bytes, extension, ""


def _decode_data_url_image(image_data_url: str) -> tuple[bytes | None, str, str]:
    raw_payload = str(image_data_url or "").strip()
    if not raw_payload:
        return None, "", ""

    match = DATA_URL_IMAGE_RE.match(raw_payload)
    if not match:
        return None, "", "Los datos de la imagen recortada no son válidos."

    mime_type = str(match.group(1) or "").strip().lower()
    extension = ALLOWED_IMAGE_MIME_TYPES.get(mime_type)
    if not extension:
        allowed = ", ".join(sorted(ALLOWED_IMAGE_MIME_TYPES))
        return None, "", f"Tipo de imagen recortada no compatible `{mime_type}`. Permitidos: {allowed}"

    base64_payload = re.sub(r"\s+", "", str(match.group(2) or ""))
    try:
        image_bytes = base64.b64decode(base64_payload, validate=True)
    except (binascii.Error, ValueError):
        return None, "", "No se pudieron decodificar los datos de la imagen recortada."

    if not image_bytes:
        return None, "", "Los datos de la imagen recortada están vacíos."
    if len(image_bytes) > MAX_IMAGE_BYTES:
        return None, "", f"La imagen supera el límite de {MAX_IMAGE_BYTES // (1024 * 1024)} MB."

    return image_bytes, extension, ""


def _save_recipe_image(slug: str, image_bytes: bytes, extension: str) -> str:
    normalized_extension = str(extension or "").strip().lower() or ".png"
    if normalized_extension not in ALLOWED_IMAGE_EXTENSIONS:
        normalized_extension = ".png"

    filename = f"{_slugify(slug)}-{int(time.time())}{normalized_extension}"
    blob_name = f"{RECIPE_IMAGES_PREFIX}/{_slugify(slug)}/{filename}"
    upload_bytes(
        image_bytes,
        blob_name,
        content_type=None,
        cache_seconds=31536000,
    )
    return media_path(blob_name)


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
        "verified": bool(recipe.get("verified")),
    }


def _recipe_from_form_inputs(
    *,
    name: str,
    tags_text: str,
    tools_text: str,
    total_time: str,
    persons: str,
    ingredients_text: str,
    steps_text: str,
    image_route: str = "",
    verified: bool = False,
) -> Dict[str, object]:
    ingredients_map = _parse_ingredients_input(ingredients_text)
    return {
        "slug": "",
        "name": str(name or "").strip() or "Nueva receta",
        "ingredients": [(ingredient, amount) for ingredient, amount in ingredients_map.items()],
        "steps": _parse_steps_input(steps_text),
        "preparation_time": "No especificado",
        "total_time": str(total_time or "").strip() or "No especificado",
        "persons": str(persons or "").strip() or "No especificado",
        "card_image": DEFAULT_CARD_COLOR,
        "card_image_file": str(image_route or "").strip(),
        "tags": _parse_inline_values(tags_text),
        "tools": _parse_inline_values(tools_text),
        "verified": bool(verified),
    }


def _empty_page_state(title_html: str, detail_html: str, page_message: str = ""):
    return (
        title_html,
        gr.update(value=EDIT_TOGGLE_BUTTON_LABEL, visible=False),
        gr.update(value=_bool_state(False)),
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
    current_slug_override: str | None = None,
    show_edit_button: bool = True,
):
    form = _build_edit_form(recipe)
    recipe_for_render = dict(recipe)
    recipe_for_render["tag_catalog"] = _collect_choices("tags")
    recipe_for_render["tool_catalog"] = _collect_choices("tools")

    return (
        f"<h2>{html.escape(str(recipe.get('name') or 'Receta'))}</h2>",
        gr.update(value=EDIT_TOGGLE_BUTTON_LABEL, visible=show_edit_button),
        gr.update(value=_bool_state(form["verified"])),
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
        current_slug_override if current_slug_override is not None else form["slug"],
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


def _new_recipe_page_state(
    *,
    page_message: str = "",
    card_message: str = "",
    seed_name: str = "",
    tags_text: str = "",
    tools_text: str = "",
    total_time: str = "",
    persons: str = "",
    ingredients_text: str = "",
    steps_text: str = "",
    image_route: str = "",
    verified_flag: bool = False,
) -> tuple[object, ...]:
    recipe = _recipe_from_form_inputs(
        name=seed_name,
        tags_text=tags_text,
        tools_text=tools_text,
        total_time=total_time,
        persons=persons,
        ingredients_text=ingredients_text,
        steps_text=steps_text,
        image_route=image_route,
        verified=verified_flag,
    )
    return _recipe_page_state(
        recipe,
        editing=True,
        page_message=page_message,
        card_message=card_message,
        current_slug_override=NEW_RECIPE_SENTINEL,
        show_edit_button=False,
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
            "<h2>Receta no encontrada</h2>",
            _render_missing_recipe(slug),
            page_message=page_message or "❌ Receta no encontrada.",
        )
    return _recipe_page_state(
        recipe,
        editing=editing,
        page_message=page_message,
        card_message=card_message,
    )


def _load_people_display_page(request: gr.Request):
    try:
        if _is_create_request(request):
            return _new_recipe_page_state()

        slug = _query_param(request, "slug").lower()
        if not slug:
            return _empty_page_state("<h2>Recetas</h2>", _render_recipe_selection_prompt())

        recipe = _fetch_recipe_by_slug(slug)
        if recipe is None:
            return _empty_page_state("<h2>Receta no encontrada</h2>", _render_missing_recipe(slug))

        return _recipe_page_state(recipe, editing=False)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to load recipe display page: %s", exc)
        return _empty_page_state(
            "<h2>Recetas</h2>",
            _render_missing_recipe("load-error"),
            page_message="❌ No se pudo cargar la receta.",
        )


def _open_edit_mode(current_slug: str):
    raw_slug = str(current_slug or "").strip()
    if raw_slug == NEW_RECIPE_SENTINEL:
        return _new_recipe_page_state()
    if not raw_slug:
        return _empty_page_state("<h2>Recetas</h2>", _render_recipe_selection_prompt())
    normalized_slug = _slugify(raw_slug)
    return _state_from_slug(normalized_slug, editing=True)


def _cancel_edit_mode(current_slug: str):
    raw_slug = str(current_slug or "").strip()
    if raw_slug == NEW_RECIPE_SENTINEL:
        return _new_recipe_page_state()
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
    recipe_verified_state: str,
    card_proposal_image: object,
    card_proposal_image_data: str,
    edit_ingredients: str,
    edit_steps: str,
    current_image_route: str,
):
    raw_slug = str(current_slug or "").strip()
    is_create_mode = raw_slug == NEW_RECIPE_SENTINEL
    if not raw_slug and not is_create_mode:
        return _empty_page_state(
            "<h2>Recetas</h2>",
            _render_recipe_selection_prompt(),
            page_message="❌ Selecciona primero una receta.",
        )
    normalized_slug = _slugify(raw_slug if not is_create_mode else card_proposal_name)
    verified_value = _is_truthy(recipe_verified_state)

    if is_create_mode and not str(card_proposal_name or "").strip():
        return _new_recipe_page_state(
            card_message="❌ Indica un nombre para la nueva receta.",
            seed_name=card_proposal_name,
            tags_text=card_proposal_tags,
            tools_text=card_proposal_tools,
            total_time=card_proposal_total_time,
            persons=card_proposal_persons,
            ingredients_text=edit_ingredients,
            steps_text=edit_steps,
            image_route=current_image_route,
            verified_flag=verified_value,
        )

    existing_payload = _read_recipe_payload(normalized_slug)
    if existing_payload is None and not is_create_mode:
        return _state_from_slug(
            normalized_slug,
            editing=True,
            card_message="❌ No se pudo guardar: no se encontró el archivo JSON de la receta.",
        )
    if is_create_mode and existing_payload is not None:
        return _new_recipe_page_state(
            card_message=(
                f"❌ Ya existe una receta con slug `{normalized_slug}`. "
                "Cambia el nombre para crear una nueva."
            ),
            seed_name=card_proposal_name,
            tags_text=card_proposal_tags,
            tools_text=card_proposal_tools,
            total_time=card_proposal_total_time,
            persons=card_proposal_persons,
            ingredients_text=edit_ingredients,
            steps_text=edit_steps,
            image_route=current_image_route,
            verified_flag=verified_value,
        )
    if existing_payload is None:
        existing_payload = {}

    clean_name = str(card_proposal_name or "").strip() or str(existing_payload.get("Name") or "Receta")
    clean_total_time = str(card_proposal_total_time or "").strip() or "No especificado"
    clean_persons = str(card_proposal_persons or "").strip() or "No especificado"
    # Card color is legacy fallback only; image is the authoritative card visual.
    clean_card_color = str(existing_payload.get("card image") or DEFAULT_CARD_COLOR).strip() or DEFAULT_CARD_COLOR
    clean_tags = _parse_inline_values(card_proposal_tags)
    clean_tools = _parse_inline_values(card_proposal_tools)
    ingredients = _parse_ingredients_input(edit_ingredients)
    steps = _parse_steps_input(edit_steps)

    image_route = str(current_image_route or existing_payload.get("card image file") or "").strip()

    cropped_bytes, cropped_ext, cropped_error = _decode_data_url_image(card_proposal_image_data)
    if cropped_error:
        if is_create_mode:
            return _new_recipe_page_state(
                card_message=f"❌ {cropped_error}",
                seed_name=card_proposal_name,
                tags_text=card_proposal_tags,
                tools_text=card_proposal_tools,
                total_time=card_proposal_total_time,
                persons=card_proposal_persons,
                ingredients_text=edit_ingredients,
                steps_text=edit_steps,
                image_route=current_image_route,
                verified_flag=verified_value,
            )
        return _state_from_slug(normalized_slug, editing=True, card_message=f"❌ {cropped_error}")

    upload_bytes, upload_ext, upload_error = _extract_upload_image_bytes(card_proposal_image)
    if upload_error:
        if is_create_mode:
            return _new_recipe_page_state(
                card_message=f"❌ {upload_error}",
                seed_name=card_proposal_name,
                tags_text=card_proposal_tags,
                tools_text=card_proposal_tools,
                total_time=card_proposal_total_time,
                persons=card_proposal_persons,
                ingredients_text=edit_ingredients,
                steps_text=edit_steps,
                image_route=current_image_route,
                verified_flag=verified_value,
            )
        return _state_from_slug(normalized_slug, editing=True, card_message=f"❌ {upload_error}")

    image_bytes = cropped_bytes or upload_bytes
    image_extension = cropped_ext or upload_ext
    if image_bytes:
        try:
            image_route = _save_recipe_image(normalized_slug, image_bytes, image_extension)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to save recipe image: %s", exc)
            if is_create_mode:
                return _new_recipe_page_state(
                    card_message="❌ No se pudo guardar la imagen.",
                    seed_name=card_proposal_name,
                    tags_text=card_proposal_tags,
                    tools_text=card_proposal_tools,
                    total_time=card_proposal_total_time,
                    persons=card_proposal_persons,
                    ingredients_text=edit_ingredients,
                    steps_text=edit_steps,
                    image_route=current_image_route,
                    verified_flag=verified_value,
                )
            return _state_from_slug(normalized_slug, editing=True, card_message="❌ No se pudo guardar la imagen.")

    next_payload: Dict[str, object] = dict(existing_payload)
    next_payload["Name"] = clean_name
    next_payload["Ingredients"] = ingredients
    next_payload["Steps"] = steps
    next_payload["Total time"] = clean_total_time
    next_payload["Nºpersonas"] = clean_persons
    next_payload["Verified"] = verified_value
    next_payload["card image"] = clean_card_color
    next_payload["Tags"] = clean_tags
    next_payload["Tools"] = clean_tools
    if image_route:
        next_payload["card image file"] = image_route
    else:
        next_payload.pop("card image file", None)

    if not _write_recipe_payload(normalized_slug, next_payload):
        if is_create_mode:
            return _new_recipe_page_state(
                card_message="❌ No se pudo guardar el archivo JSON de la receta.",
                seed_name=card_proposal_name,
                tags_text=card_proposal_tags,
                tools_text=card_proposal_tools,
                total_time=card_proposal_total_time,
                persons=card_proposal_persons,
                ingredients_text=edit_ingredients,
                steps_text=edit_steps,
                image_route=current_image_route,
                verified_flag=verified_value,
            )
        return _state_from_slug(
            normalized_slug,
            editing=True,
            card_message="❌ No se pudo guardar el archivo JSON de la receta.",
        )

    if is_create_mode:
        return _state_from_slug(
            normalized_slug,
            editing=False,
            page_message="✅ Receta creada.",
        )

    return _state_from_slug(
        normalized_slug,
        editing=False,
        page_message="✅ Receta actualizada.",
    )


def _toggle_recipe_verified_from_detail(
    payload_json: str,
    current_slug: str,
    recipe_verified_state: str,
):
    try:
        payload = json.loads(payload_json or "{}")
    except json.JSONDecodeError:
        payload = {}

    has_next_state = "nextState" in payload
    next_value = _is_truthy(payload.get("nextState")) if has_next_state else _is_truthy(recipe_verified_state)

    current_slug_raw = str(current_slug or "").strip()
    payload_slug = str(payload.get("slug") or "").strip()
    resolved_slug = payload_slug or current_slug_raw
    if resolved_slug == NEW_RECIPE_SENTINEL:
        return (
            gr.update(),
            gr.update(value="ℹ️ Esta verificación se guardará cuando crees la receta.", visible=True),
            gr.update(value=_bool_state(next_value)),
        )
    if not resolved_slug:
        return (
            gr.update(),
            gr.update(value="❌ Selecciona una receta para marcarla.", visible=True),
            gr.update(value=_bool_state(False)),
        )

    normalized_slug = _slugify(resolved_slug)
    recipe_payload = _read_recipe_payload(normalized_slug)
    if recipe_payload is None:
        return (
            gr.update(),
            gr.update(value="❌ No se encontró la receta.", visible=True),
            gr.update(value=_bool_state(recipe_verified_state)),
        )

    current_value = _is_truthy(
        recipe_payload.get("Verified") if "Verified" in recipe_payload else recipe_payload.get("verified")
    )
    if current_value != next_value:
        recipe_payload["Verified"] = next_value
        if not _write_recipe_payload(normalized_slug, recipe_payload):
            return (
                gr.update(),
                gr.update(value="❌ No se pudo actualizar la verificación.", visible=True),
                gr.update(value=_bool_state(current_value)),
            )

    recipe = _fetch_recipe_by_slug(normalized_slug)
    if recipe is None:
        return (
            gr.update(),
            gr.update(value="✅ Verificación actualizada.", visible=True),
            gr.update(value=_bool_state(next_value)),
        )

    recipe_for_render = dict(recipe)
    recipe_for_render["tag_catalog"] = _collect_choices("tags")
    recipe_for_render["tool_catalog"] = _collect_choices("tools")
    state_text = "verificada" if next_value else "sin verificar"
    return (
        gr.update(value=_render_recipe_hero(recipe_for_render), visible=True),
        gr.update(value=f"✅ Receta marcada como {state_text}.", visible=True),
        gr.update(value=_bool_state(next_value)),
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
                    card_proposal_name = gr.Textbox(label="Nombre de la tarjeta", elem_id="the-list-card-proposal-name")
                    card_proposal_bucket = gr.Textbox(label="Título de la tarjeta", elem_id="the-list-card-proposal-bucket")

                card_proposal_tags = gr.Textbox(
                    label="Etiquetas de la tarjeta",
                    lines=2,
                    placeholder="Etiquetas separadas por comas",
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
                    gr.Markdown("**Imagen de la tarjeta**")
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
                        placeholder="3 maduros | aguacate",
                        elem_id="recipe-editor-ingredients-input",
                    )
                    edit_steps_input = gr.Textbox(
                        label="Preparación",
                        show_label=False,
                        lines=14,
                        placeholder="Tritura los aguacates en un bol hasta que queden casi cremosos.",
                        elem_id="recipe-editor-steps-input",
                    )

                with gr.Row(elem_id="the-list-card-proposal-actions"):
                    submit_btn = gr.Button("Guardar", variant="primary")
                    cancel_btn = gr.Button("Cancelar", variant="secondary")

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
            recipe_verified_state = gr.Textbox(
                value=_bool_state(False),
                visible=False,
                interactive=False,
                elem_id="recipe-verified-state",
            )
            detail_verify_payload = gr.Textbox(
                value="",
                visible=False,
                interactive=False,
                elem_id="recipe-detail-verify-payload",
            )
            detail_verify_trigger = gr.Button(
                "_toggle_recipe_verified_detail",
                visible=False,
                elem_id="recipe-detail-verify-trigger",
            )

        page_state_outputs = [
            title_md,
            edit_btn,
            recipe_verified_state,
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
                recipe_verified_state,
                card_proposal_image,
                card_proposal_image_data,
                edit_ingredients_input,
                edit_steps_input,
                image_route_state,
            ],
            outputs=page_state_outputs,
            show_progress=False,
        )
        detail_verify_trigger.click(
            timed_page_load(
                "/receta",
                _toggle_recipe_verified_from_detail,
                label="toggle_recipe_verified_from_detail",
            ),
            inputs=[detail_verify_payload, current_slug, recipe_verified_state],
            outputs=[detail_html, page_status, recipe_verified_state],
            show_progress=False,
        )

    return app
