from __future__ import annotations

import base64
import binascii
import html
import json
import logging
import os
import re
import tempfile
import textwrap
import time
from pathlib import Path
from typing import Dict, List, Sequence
from urllib.parse import unquote, urlparse
from uuid import uuid4

import gradio as gr
from PIL import Image, ImageDraw, ImageFont

from src.gcs_storage import delete_prefix, get_bucket, media_path, upload_bytes
from src.page_timing import timed_page_load
from src.pages.header import render_header, with_light_mode_head
from src.recipe_importer import import_recipe_from_path_or_text, recipe_payload_to_form_values
from src.pages.recetas_list.core_the_list import (
    DEFAULT_CARD_COLOR,
    _fetch_all_people,
    _fetch_recipe_by_slug,
    _query_param,
    _read_recipe_index_csv_rows,
    _read_recipe_payload,
    _rebuild_recipe_index_from_bucket,
    _recipe_blob_name_for_slug,
    _render_recipe_markdown,
    _render_recipe_hero,
    _slugify,
    _write_recipe_index_csv_rows,
    _write_recipe_payload,
)

logger = logging.getLogger(__name__)

ASSETS_DIR = Path(__file__).resolve().parent
CSS_PATH = ASSETS_DIR / "css" / "people_display_page.css"
EDITOR_JS_PATH = ASSETS_DIR / "js" / "people_editor.js"
RECIPE_IMAGES_PREFIX = (os.getenv("RECETAS_IMAGES_PREFIX") or "recipes/images").strip("/ ")
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
RECIPE_EXPORT_DIR = Path(tempfile.gettempdir()) / "recetas_exports"
RECIPE_EXPORT_FONT_PATHS = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
)


def _read_asset(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.warning("Missing recipe display asset at %s", path)
        return ""


def _load_css() -> str:
    return _read_asset(CSS_PATH)


def _is_truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "si", "sí"}


def _load_editor_js() -> str:
    script = _read_asset(EDITOR_JS_PATH)
    if not script:
        return ""
    return f"<script>\n{script}\n</script>"


def _header_people_display(request: gr.Request):
    return render_header(path="/recetas", request=request)


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


def _normalize_recipe_slug(raw_slug: str) -> str:
    raw = str(raw_slug or "").strip()
    if not raw or raw == NEW_RECIPE_SENTINEL:
        return ""
    return _slugify(raw)


def _recipe_export_basename(slug: str, payload: Dict[str, object]) -> str:
    display_name = str(payload.get("Name") or "").strip()
    if display_name:
        return _slugify(display_name)
    return _slugify(slug or "receta")


def _recipe_export_path(slug: str, payload: Dict[str, object], extension: str) -> Path:
    RECIPE_EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    normalized_extension = str(extension or "").strip().lstrip(".").lower() or "txt"
    basename = _recipe_export_basename(slug, payload)
    return RECIPE_EXPORT_DIR / f"{basename}-{uuid4().hex[:8]}.{normalized_extension}"


def _recipe_for_export(slug: str, payload: Dict[str, object]) -> Dict[str, object]:
    recipe = _fetch_recipe_by_slug(slug)
    if recipe is not None:
        return recipe

    ingredients_payload = payload.get("Ingredients")
    if isinstance(ingredients_payload, dict):
        ingredients = [
            (str(ingredient_name or "").strip(), str(amount or "").strip())
            for ingredient_name, amount in ingredients_payload.items()
        ]
    else:
        ingredients = []

    steps_payload = payload.get("Steps")
    steps = [str(step or "").strip() for step in steps_payload] if isinstance(steps_payload, list) else []

    return {
        "slug": slug,
        "name": str(payload.get("Name") or "Receta").strip() or "Receta",
        "total_time": str(payload.get("Total time") or "No especificado").strip() or "No especificado",
        "persons": str(payload.get("Nºpersonas") or "No especificado").strip() or "No especificado",
        "tags": _normalize_values(payload.get("Tags") if isinstance(payload.get("Tags"), list) else []),
        "ingredients": ingredients,
        "steps": steps,
        "card_image_file": str(payload.get("card image file") or payload.get("card_image_file") or "").strip(),
    }


def _recipe_export_html_document(recipe: Dict[str, object]) -> str:
    name = html.escape(str(recipe.get("name") or "Receta"))
    total_time = html.escape(str(recipe.get("total_time") or "No especificado"))
    persons = html.escape(str(recipe.get("persons") or "No especificado"))
    tags = ", ".join(html.escape(str(tag or "")) for tag in recipe.get("tags", []) if str(tag or "").strip())
    image_route = html.escape(str(recipe.get("card_image_file") or ""), quote=True)
    ingredients_items = []
    for ingredient_name, amount in recipe.get("ingredients", []):
        amount_text = html.escape(str(amount or "").strip())
        ingredient_text = html.escape(str(ingredient_name or "").strip())
        if amount_text and ingredient_text:
            ingredients_items.append(f"<li>{amount_text} - {ingredient_text}</li>")
        elif ingredient_text:
            ingredients_items.append(f"<li>{ingredient_text}</li>")
        elif amount_text:
            ingredients_items.append(f"<li>{amount_text}</li>")
    if not ingredients_items:
        ingredients_items.append("<li>Sin ingredientes especificados.</li>")

    steps_items = []
    for step in recipe.get("steps", []):
        step_text = html.escape(str(step or "").strip())
        if step_text:
            steps_items.append(f"<li>{step_text}</li>")
    if not steps_items:
        steps_items.append("<li>Sin pasos especificados.</li>")

    image_markup = f"<img src='{image_route}' alt='{name}'/>" if image_route else ""
    tags_text = tags or "Sin etiquetas"

    return f"""<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>{name}</title>
  <style>
    body {{
      margin: 0;
      padding: 2rem;
      font-family: "IBM Plex Sans", "Segoe UI", sans-serif;
      color: #1f2933;
      background: #f8fafc;
    }}
    main {{
      max-width: 880px;
      margin: 0 auto;
      background: #ffffff;
      border: 1px solid #d5d7d8;
      border-radius: 14px;
      padding: 1.4rem;
    }}
    h1 {{
      margin: 0 0 0.7rem 0;
      color: #154734;
      font-size: 2.2rem;
      line-height: 1.1;
    }}
    img {{
      max-width: 100%;
      border-radius: 10px;
      border: 1px solid #d5d7d8;
      margin: 0.5rem 0 1rem 0;
    }}
    .meta {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 0.6rem 1rem;
      margin-bottom: 1rem;
    }}
    .chip-row {{
      margin: 0.25rem 0 1rem 0;
      font-size: 0.95rem;
    }}
    section {{
      margin-top: 1rem;
    }}
    h2 {{
      margin: 0 0 0.5rem 0;
      color: #154734;
      font-size: 1.3rem;
    }}
    li {{
      margin-bottom: 0.25rem;
    }}
  </style>
</head>
<body>
  <main>
    <h1>{name}</h1>
    {image_markup}
    <div class="meta">
      <div><strong>Tiempo total:</strong> {total_time}</div>
      <div><strong>Nº personas:</strong> {persons}</div>
    </div>
    <div class="chip-row"><strong>Etiquetas:</strong> {tags_text}</div>
    <section>
      <h2>Ingredientes</h2>
      <ul>
        {"".join(ingredients_items)}
      </ul>
    </section>
    <section>
      <h2>Preparación</h2>
      <ol>
        {"".join(steps_items)}
      </ol>
    </section>
  </main>
</body>
</html>
"""


def _recipe_export_text_lines(recipe: Dict[str, object]) -> List[str]:
    lines: List[str] = []
    lines.append(str(recipe.get("name") or "Receta").strip() or "Receta")
    lines.append("")
    lines.append(f"Tiempo total: {str(recipe.get('total_time') or 'No especificado').strip() or 'No especificado'}")
    lines.append(f"Nº personas: {str(recipe.get('persons') or 'No especificado').strip() or 'No especificado'}")
    tags = [str(tag or "").strip() for tag in recipe.get("tags", []) if str(tag or "").strip()]
    lines.append(f"Etiquetas: {', '.join(tags) if tags else 'Sin etiquetas'}")
    lines.append("")
    lines.append("Ingredientes")
    ingredients = recipe.get("ingredients", [])
    if ingredients:
        for ingredient_name, amount in ingredients:
            amount_text = str(amount or "").strip()
            ingredient_text = str(ingredient_name or "").strip()
            if amount_text and ingredient_text:
                lines.append(f"- {amount_text} - {ingredient_text}")
            elif ingredient_text:
                lines.append(f"- {ingredient_text}")
            elif amount_text:
                lines.append(f"- {amount_text}")
    else:
        lines.append("- Sin ingredientes especificados.")
    lines.append("")
    lines.append("Preparación")
    steps = recipe.get("steps", [])
    if steps:
        for index, step in enumerate(steps, start=1):
            step_text = str(step or "").strip()
            if step_text:
                lines.append(f"{index}. {step_text}")
    else:
        lines.append("1. Sin pasos especificados.")
    return lines


def _load_recipe_export_font(font_size: int = 24) -> ImageFont.ImageFont:
    size = max(12, int(font_size))
    for font_path in RECIPE_EXPORT_FONT_PATHS:
        candidate = Path(font_path)
        if not candidate.is_file():
            continue
        try:
            return ImageFont.truetype(str(candidate), size=size)
        except Exception:
            logger.debug("Could not load export font `%s`.", candidate, exc_info=True)
    return ImageFont.load_default()


def _wrap_recipe_export_line(value: str, line_width: int) -> List[str]:
    text = str(value or "").rstrip()
    if not text:
        return [""]

    bullet_prefix = ""
    bullet_indent = ""
    body = text

    if text.startswith("- "):
        bullet_prefix = "- "
        bullet_indent = "  "
        body = text[2:]
    else:
        ordered_match = re.match(r"^(\d+\.\s+)(.+)$", text)
        if ordered_match:
            bullet_prefix = ordered_match.group(1)
            bullet_indent = " " * len(bullet_prefix)
            body = ordered_match.group(2)

    width = max(18, int(line_width) - len(bullet_prefix))
    wrapped = textwrap.wrap(body, width=width, break_long_words=False, break_on_hyphens=False)
    if not wrapped:
        return [bullet_prefix.rstrip()]

    lines = [f"{bullet_prefix}{wrapped[0]}"]
    for chunk in wrapped[1:]:
        lines.append(f"{bullet_indent}{chunk}")
    return lines


def _write_recipe_pdf_export(path: Path, recipe: Dict[str, object]) -> None:
    page_width = 1240
    page_height = 1754
    margin = 84
    font = _load_recipe_export_font(24)
    line_height = 34
    max_lines_per_page = max(20, (page_height - (margin * 2)) // line_height)
    max_chars_per_line = 90

    wrapped_lines: List[str] = []
    for raw_line in _recipe_export_text_lines(recipe):
        wrapped_lines.extend(_wrap_recipe_export_line(raw_line, max_chars_per_line))

    if not wrapped_lines:
        wrapped_lines = ["Receta"]

    pages: List[Image.Image] = []
    for start in range(0, len(wrapped_lines), max_lines_per_page):
        page_lines = wrapped_lines[start:start + max_lines_per_page]
        page = Image.new("RGB", (page_width, page_height), "white")
        draw = ImageDraw.Draw(page)
        y = margin
        for line in page_lines:
            draw.text((margin, y), line, fill="#111827", font=font)
            y += line_height
        pages.append(page)

    first_page, *remaining_pages = pages
    first_page.save(path, "PDF", save_all=True, append_images=remaining_pages, resolution=150.0)


def _export_recipe_download_file(current_slug: str, export_format: str):
    normalized_slug = _normalize_recipe_slug(current_slug)
    if not normalized_slug:
        return "", gr.update(value="❌ Selecciona primero una receta.", visible=True)

    payload = _read_recipe_payload(normalized_slug)
    if payload is None:
        return "", gr.update(value="❌ No se encontró la receta para exportar.", visible=True)

    recipe = _recipe_for_export(normalized_slug, payload)
    normalized_format = str(export_format or "").strip().lower()

    try:
        if normalized_format == "json":
            export_path = _recipe_export_path(normalized_slug, payload, "json")
            body = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
            export_path.write_text(body, encoding="utf-8")
            mime_type = "application/json"
        elif normalized_format == "html":
            export_path = _recipe_export_path(normalized_slug, payload, "html")
            export_path.write_text(_recipe_export_html_document(recipe), encoding="utf-8")
            mime_type = "text/html"
        elif normalized_format == "pdf":
            export_path = _recipe_export_path(normalized_slug, payload, "pdf")
            _write_recipe_pdf_export(export_path, recipe)
            mime_type = "application/pdf"
        else:
            return "", gr.update(value="❌ Formato de descarga no soportado.", visible=True)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed exporting recipe `%s` as `%s`: %s", normalized_slug, normalized_format, exc)
        return "", gr.update(value=f"❌ No se pudo preparar la descarga: {exc}", visible=True)

    payload_json = json.dumps(
        {
            "filename": export_path.name,
            "mime_type": mime_type,
            "data_base64": base64.b64encode(export_path.read_bytes()).decode("ascii"),
        },
        ensure_ascii=True,
    )
    return payload_json, gr.update(value=f"✅ Descarga {normalized_format.upper()} preparada.", visible=True)


def _download_recipe_json(current_slug: str):
    return _export_recipe_download_file(current_slug, "json")


def _download_recipe_html(current_slug: str):
    return _export_recipe_download_file(current_slug, "html")


def _download_recipe_pdf(current_slug: str):
    return _export_recipe_download_file(current_slug, "pdf")


def _resolve_media_blob_name(raw_value: str) -> str:
    text = str(raw_value or "").strip()
    if not text:
        return ""
    parsed = urlparse(text)
    if parsed.scheme:
        path_value = parsed.path
    else:
        path_value = text
    normalized = unquote(path_value).lstrip("/")
    if normalized.startswith("media/"):
        normalized = normalized[len("media/"):]
    return normalized


def _index_slug(row: Dict[str, str]) -> str:
    return _slugify(str(row.get("slug") or row.get("recipe_name") or ""))


def _rows_for_slug(rows: Sequence[Dict[str, str]], slug: str) -> List[Dict[str, str]]:
    normalized_slug = _slugify(slug)
    if not normalized_slug:
        return []
    return [row for row in rows if _index_slug(row) == normalized_slug]


def _remove_recipe_row_from_index_csv(slug: str) -> bool:
    normalized_slug = _slugify(slug)
    if not normalized_slug:
        return False

    rows = _read_recipe_index_csv_rows()
    if rows is None:
        return False

    filtered_rows = [row for row in rows if _index_slug(row) != normalized_slug]
    if len(filtered_rows) == len(rows):
        return True
    return _write_recipe_index_csv_rows(filtered_rows)


def _ensure_recipe_index_row_removed(slug: str) -> bool:
    normalized_slug = _slugify(slug)
    if not normalized_slug:
        return False

    csv_updated = _remove_recipe_row_from_index_csv(normalized_slug)
    if csv_updated:
        return True

    logger.warning("Direct recipe index row removal failed for `%s`; trying full rebuild.", normalized_slug)
    try:
        _rebuild_recipe_index_from_bucket()
    except Exception:
        logger.exception("Failed rebuilding recipe index after deleting `%s`", normalized_slug)
    reloaded_rows = _read_recipe_index_csv_rows() or []
    return not bool(_rows_for_slug(reloaded_rows, normalized_slug))


def _cleanup_recipe_images(
    *,
    bucket,
    normalized_slug: str,
    possible_image_values: Sequence[object],
) -> tuple[int, int, bool]:
    deleted_direct_image_blobs = 0
    deleted_prefix_image_blobs = 0
    had_image_references = False
    try:
        image_blob_names: set[str] = set()
        for raw_image_value in possible_image_values:
            blob_name = _resolve_media_blob_name(str(raw_image_value or ""))
            if not blob_name:
                continue
            image_blob_names.add(blob_name)
        had_image_references = bool(image_blob_names)

        for blob_name in sorted(image_blob_names):
            try:
                bucket.blob(blob_name).delete()
                deleted_direct_image_blobs += 1
            except Exception:
                logger.debug("Could not delete image blob `%s` for recipe `%s`", blob_name, normalized_slug, exc_info=True)

        prefix_candidates: set[str] = {f"{RECIPE_IMAGES_PREFIX}/{normalized_slug}/"}
        for blob_name in image_blob_names:
            parent_prefix = blob_name.rsplit("/", 1)[0].strip("/")
            if parent_prefix.endswith(f"/{normalized_slug}"):
                prefix_candidates.add(f"{parent_prefix}/")

        for prefix in sorted(prefix_candidates):
            deleted_prefix_image_blobs += delete_prefix(prefix)
    except Exception:
        logger.debug("Recipe image cleanup failed for `%s`", normalized_slug, exc_info=True)

    return deleted_direct_image_blobs, deleted_prefix_image_blobs, had_image_references


def _delete_recipe_from_detail(current_slug: str):
    normalized_slug = _normalize_recipe_slug(current_slug)
    if not normalized_slug:
        return _empty_page_state(
            "<h2>Recetas</h2>",
            _render_recipe_selection_prompt(),
            page_message="❌ Selecciona primero una receta.",
        )

    index_rows = _read_recipe_index_csv_rows() or []
    matching_index_rows = _rows_for_slug(index_rows, normalized_slug)
    recipe_payload = _read_recipe_payload(normalized_slug)
    bucket = get_bucket()
    if recipe_payload is None:
        # Handle stale index entries where the recipe JSON is already gone.
        try:
            bucket.blob(_recipe_blob_name_for_slug(normalized_slug)).delete()
        except Exception:
            logger.debug("Recipe blob `%s` already missing or could not be deleted.", normalized_slug, exc_info=True)

        (
            deleted_direct_image_blobs,
            deleted_prefix_image_blobs,
            had_image_references,
        ) = _cleanup_recipe_images(
            bucket=bucket,
            normalized_slug=normalized_slug,
            possible_image_values=[row.get("image_location_in_bucket") for row in matching_index_rows],
        )
        csv_updated = _ensure_recipe_index_row_removed(normalized_slug)
        if not matching_index_rows and not csv_updated:
            return _empty_page_state(
                "<h2>Receta no encontrada</h2>",
                _render_missing_recipe(normalized_slug),
                page_message="❌ No se encontró la receta para eliminar.",
            )

        page_message = f"✅ Receta `{normalized_slug}` eliminada del índice."
        if not csv_updated:
            page_message += " ⚠️ No se pudo confirmar que el CSV de índices se haya actualizado."
        elif had_image_references and (deleted_direct_image_blobs + deleted_prefix_image_blobs) <= 0:
            page_message += " ⚠️ No se detectaron blobs de imagen para borrar."
        return _empty_page_state(
            "<h2>Recetas</h2>",
            _render_recipe_selection_prompt(),
            page_message=page_message,
        )

    is_verified = _is_truthy(
        recipe_payload.get("Verified") if "Verified" in recipe_payload else recipe_payload.get("verified")
    )
    if is_verified:
        return _state_from_slug(
            normalized_slug,
            editing=False,
            page_message=(
                "❌ No se puede eliminar una receta verificada. "
                "Primero márcala como sin verificar."
            ),
        )

    recipe_blob_name = _recipe_blob_name_for_slug(normalized_slug)

    try:
        bucket.blob(recipe_blob_name).delete()
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed deleting recipe blob `%s`: %s", recipe_blob_name, exc)
        return _state_from_slug(
            normalized_slug,
            editing=False,
            page_message=f"❌ No se pudo eliminar la receta: {exc}",
        )

    (
        deleted_direct_image_blobs,
        deleted_prefix_image_blobs,
        had_image_references,
    ) = _cleanup_recipe_images(
        bucket=bucket,
        normalized_slug=normalized_slug,
        possible_image_values=[
            recipe_payload.get("card image file"),
            recipe_payload.get("card_image_file"),
            recipe_payload.get("image_location_in_bucket"),
            *[row.get("image_location_in_bucket") for row in matching_index_rows],
        ],
    )

    csv_updated = _ensure_recipe_index_row_removed(normalized_slug)

    page_message = f"✅ Receta `{normalized_slug}` eliminada."
    if not csv_updated:
        page_message += " ⚠️ No se pudo confirmar que el CSV de índices se haya actualizado."
    elif had_image_references and (deleted_direct_image_blobs + deleted_prefix_image_blobs) <= 0:
        page_message += " ⚠️ No se detectaron blobs de imagen para borrar."

    return _empty_page_state(
        "<h2>Recetas</h2>",
        _render_recipe_selection_prompt(),
        page_message=page_message,
    )


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


def _extract_upload_paths(uploaded_files: object) -> List[str]:
    paths: List[str] = []
    seen: set[str] = set()

    def _walk(value: object):
        if value is None or value is False:
            return
        if isinstance(value, (list, tuple)):
            for item in value:
                _walk(item)
            return
        candidate = _extract_upload_path(value).strip()
        if not candidate or candidate in seen:
            return
        seen.add(candidate)
        paths.append(candidate)

    _walk(uploaded_files)
    return paths


def _render_recipe_import_file_list(uploaded_files: object):
    selected_paths = _extract_upload_paths(uploaded_files)
    if not selected_paths:
        return (
            gr.update(value="", visible=False),
            gr.update(value="", visible=False),
        )

    file_items = "".join(
        (
            "<li>"
            "<span class='recipe-import-file-chip'>"
            f"<span class='recipe-import-file-chip__label'>{html.escape(Path(path).name)}</span>"
            "<button "
            "type='button' "
            "class='recipe-import-file-chip__remove' "
            f"data-recipe-import-remove-index='{index}' "
            "aria-label='Quitar archivo'>x</button>"
            "</span>"
            "</li>"
        )
        for index, path in enumerate(selected_paths)
    )
    summary = f"{len(selected_paths)} archivo{'s' if len(selected_paths) != 1 else ''} seleccionado{'s' if len(selected_paths) != 1 else ''}."
    html_value = (
        "<div class='recipe-import-file-list__wrap'>"
        f"<div class='recipe-import-file-list__summary'>{html.escape(summary)}</div>"
        f"<ul class='recipe-import-file-list__items'>{file_items}</ul>"
        "</div>"
    )
    return (
        gr.update(value=html_value, visible=True),
        gr.update(value="", visible=False),
    )


def _append_recipe_import_file_selection(current_selected_files: object, uploaded_files: object):
    existing_paths = _extract_upload_paths(current_selected_files)
    incoming_paths = _extract_upload_paths(uploaded_files)
    merged_paths: List[str] = list(existing_paths)
    seen: set[str] = set(existing_paths)
    for path in incoming_paths:
        if path in seen:
            continue
        seen.add(path)
        merged_paths.append(path)

    file_list_update, status_update = _render_recipe_import_file_list(merged_paths)
    return (
        merged_paths,
        file_list_update,
        status_update,
    )


def _remove_recipe_import_file_selection(current_selected_files: object, remove_index: str):
    selected_paths = _extract_upload_paths(current_selected_files)
    try:
        index = int(str(remove_index or "").strip())
    except ValueError:
        file_list_update, status_update = _render_recipe_import_file_list(selected_paths)
        return (
            selected_paths,
            file_list_update,
            status_update,
        )

    if 0 <= index < len(selected_paths):
        selected_paths = [path for i, path in enumerate(selected_paths) if i != index]

    file_list_update, status_update = _render_recipe_import_file_list(selected_paths)
    return (
        selected_paths,
        file_list_update,
        status_update,
    )


def _is_unspecified_recipe_value(value: object) -> bool:
    text = str(value or "").strip().lower()
    return text in {"", "no especificado", "receta"}


def _merge_recipe_import_payloads(payloads: Sequence[Dict[str, object]]) -> Dict[str, object]:
    merged: Dict[str, object] = {
        "Name": "Receta",
        "Ingredients": {},
        "Steps": [],
        "Preparation time": "No especificado",
        "Total time": "No especificado",
        "Nºpersonas": "No especificado",
        "card image": DEFAULT_CARD_COLOR,
        "Tags": [],
    }

    merged_ingredients: Dict[str, str] = {}
    ingredient_seen: set[str] = set()
    step_seen: set[str] = set()
    tag_seen: set[str] = set()

    for payload in payloads:
        if not isinstance(payload, dict):
            continue

        candidate_name = str(payload.get("Name") or "").strip()
        if candidate_name and _is_unspecified_recipe_value(merged.get("Name")) and not _is_unspecified_recipe_value(candidate_name):
            merged["Name"] = candidate_name

        for key in ("Preparation time", "Total time", "Nºpersonas"):
            if _is_unspecified_recipe_value(merged.get(key)) and not _is_unspecified_recipe_value(payload.get(key)):
                merged[key] = str(payload.get(key) or "").strip()

        card_color = str(payload.get("card image") or payload.get("card_image") or "").strip()
        if card_color and str(merged.get("card image") or "").strip() == DEFAULT_CARD_COLOR:
            merged["card image"] = card_color

        raw_ingredients = payload.get("Ingredients")
        if isinstance(raw_ingredients, dict):
            for raw_name, raw_amount in raw_ingredients.items():
                ingredient_name = str(raw_name or "").strip()
                ingredient_amount = str(raw_amount or "").strip()
                if not ingredient_name and not ingredient_amount:
                    continue
                ingredient_key = ingredient_name.lower()
                if ingredient_key and ingredient_key not in ingredient_seen:
                    ingredient_seen.add(ingredient_key)
                    merged_ingredients[ingredient_name] = ingredient_amount
                    continue
                if ingredient_key:
                    existing_value = str(merged_ingredients.get(ingredient_name) or "").strip()
                    if not existing_value and ingredient_amount:
                        merged_ingredients[ingredient_name] = ingredient_amount

        raw_steps = payload.get("Steps")
        if isinstance(raw_steps, list):
            for raw_step in raw_steps:
                step_text = str(raw_step or "").strip()
                if not step_text:
                    continue
                step_key = step_text.lower()
                if step_key in step_seen:
                    continue
                step_seen.add(step_key)
                cast_steps = merged.setdefault("Steps", [])
                if isinstance(cast_steps, list):
                    cast_steps.append(step_text)

        raw_tags = payload.get("Tags")
        if isinstance(raw_tags, list):
            target = merged.setdefault("Tags", [])
            if isinstance(target, list):
                for item in raw_tags:
                    text = str(item or "").strip().lower()
                    if not text or text in tag_seen:
                        continue
                    tag_seen.add(text)
                    target.append(text)

    merged["Ingredients"] = merged_ingredients
    return merged


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
    return {
        "slug": str(recipe.get("slug") or "").strip(),
        "name": str(recipe.get("name") or "").strip(),
        "bucket": "",
        "tags_text": ", ".join(tags),
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
    total_time: str,
    persons: str,
    ingredients_text: str,
    steps_text: str,
    image_route: str = "",
    verified: bool = True,
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
        "verified": bool(verified),
    }


def _empty_page_state(title_html: str, detail_html: str, page_message: str = ""):
    return (
        title_html,
        gr.update(value=EDIT_TOGGLE_BUTTON_LABEL, visible=False),
        gr.update(visible=False),
        gr.update(value="", visible=False),
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
        gr.update(value="", visible=False),
        gr.update(value=None),
        gr.update(value="Upload", visible=False),
        gr.update(visible=False),
        gr.update(visible=False),
        gr.update(value="", visible=False),
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
    show_top_actions = show_edit_button and bool(form["slug"])
    recipe_for_render = dict(recipe)
    recipe_for_render["tag_catalog"] = _collect_choices("tags")

    return (
        f"<h2>{html.escape(str(recipe.get('name') or 'Receta'))}</h2>",
        gr.update(value=EDIT_TOGGLE_BUTTON_LABEL, visible=show_edit_button),
        gr.update(visible=show_top_actions),
        gr.update(value="", visible=show_top_actions, interactive=(show_top_actions and not form["verified"])),
        gr.update(value=_bool_state(form["verified"])),
        gr.update(value=page_message, visible=bool(page_message)),
        gr.update(value=_render_recipe_hero(recipe_for_render), visible=True),
        gr.update(value=_render_recipe_markdown(recipe), visible=not editing),
        gr.update(visible=editing),
        CARD_EDITOR_HELP,
        form["name"],
        form["bucket"],
        form["tags_text"],
        form["total_time"],
        form["persons"],
        "",
        form["ingredients"],
        form["steps"],
        current_slug_override if current_slug_override is not None else form["slug"],
        form["name"],
        form["bucket"],
        form["tags_text"],
        form["total_time"],
        form["persons"],
        form["image_route"],
        gr.update(value=card_message, visible=bool(card_message)),
        gr.update(value=None),
        gr.update(value="Upload", visible=editing),
        gr.update(visible=False),
        gr.update(visible=False),
        gr.update(value="", visible=False),
    )


def _new_recipe_page_state(
    *,
    page_message: str = "",
    card_message: str = "",
    seed_name: str = "",
    tags_text: str = "",
    total_time: str = "",
    persons: str = "",
    ingredients_text: str = "",
    steps_text: str = "",
    image_route: str = "",
    verified_flag: bool = True,
) -> tuple[object, ...]:
    recipe = _recipe_from_form_inputs(
        name=seed_name,
        tags_text=tags_text,
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

        normalized_slug = _slugify(slug)
        recipe = _fetch_recipe_by_slug(normalized_slug)
        if recipe is None:
            index_rows = _read_recipe_index_csv_rows() or []
            had_stale_index_row = bool(_rows_for_slug(index_rows, normalized_slug))
            if had_stale_index_row:
                csv_updated = _ensure_recipe_index_row_removed(normalized_slug)
                if csv_updated:
                    message = "⚠️ La receta no existe. Se eliminó su entrada huérfana del índice."
                else:
                    message = (
                        "❌ Receta no encontrada. "
                        "Además no se pudo confirmar la limpieza de su entrada en el índice."
                    )
                return _empty_page_state(
                    "<h2>Receta no encontrada</h2>",
                    _render_missing_recipe(normalized_slug),
                    page_message=message,
                )
            return _empty_page_state("<h2>Receta no encontrada</h2>", _render_missing_recipe(normalized_slug))

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
    clean_tags = _parse_inline_values(card_proposal_tags)
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
    next_payload.pop("card image", None)
    next_payload.pop("card_image", None)
    next_payload["Tags"] = clean_tags
    next_payload.pop("Tools", None)
    next_payload.pop("tools", None)
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


def _import_recipe_into_editor(
    imported_files: object,
    imported_text: str,
):
    no_change = (
        gr.update(),
        gr.update(),
        gr.update(),
        gr.update(),
        gr.update(),
        gr.update(),
        gr.update(),
        gr.update(),
        gr.update(),
    )
    upload_paths = _extract_upload_paths(imported_files)
    raw_text = str(imported_text or "").strip()
    if not upload_paths and not raw_text:
        return (
            *no_change[:-2],
            gr.update(value="❌ Sube un archivo o pega texto para importar.", visible=True),
            gr.update(value="❌ Sube un archivo o pega texto para importar.", visible=True),
        )

    try:
        imported_payloads: List[Dict[str, object]] = []
        success_labels: List[str] = []
        failed_labels: List[str] = []
        image_data_url = ""
        image_source = ""

        for upload_path in upload_paths:
            try:
                imported = import_recipe_from_path_or_text(upload_path, "", force_english=False)
            except Exception as file_exc:  # noqa: BLE001
                logger.exception("Recipe file import failed (%s): %s", upload_path, file_exc)
                failed_labels.append(f"{Path(upload_path).name}: {file_exc}")
                continue

            payload = dict(imported.get("payload") or {})
            imported_payloads.append(payload)
            source_label = str(imported.get("source_label") or Path(upload_path).name).strip()
            success_labels.append(source_label)
            candidate_image_data_url = str(imported.get("image_data_url") or "").strip()
            candidate_image_source = str(imported.get("image_source") or "").strip()
            if candidate_image_data_url and not image_data_url:
                image_data_url = candidate_image_data_url
                image_source = candidate_image_source

        if raw_text:
            try:
                text_import = import_recipe_from_path_or_text(None, raw_text, force_english=False)
                imported_payloads.append(dict(text_import.get("payload") or {}))
                success_labels.append("texto manual")
            except Exception as text_exc:  # noqa: BLE001
                logger.exception("Manual text import failed: %s", text_exc)
                if imported_payloads:
                    failed_labels.append(f"texto manual: {text_exc}")
                else:
                    raise

        if not imported_payloads:
            raise ValueError("No se pudo importar ningún archivo ni texto válido.")

        merged_payload = _merge_recipe_import_payloads(imported_payloads)
        form_values = recipe_payload_to_form_values(merged_payload)

        details: list[str] = []
        if upload_paths:
            ok_count = len(success_labels) - (1 if raw_text and "texto manual" in success_labels else 0)
            if ok_count:
                details.append(
                    f"✅ {ok_count} archivo{'s' if ok_count != 1 else ''} analizado{'s' if ok_count != 1 else ''}."
                )
            if failed_labels:
                details.append(
                    f"⚠️ {len(failed_labels)} archivo{'s' if len(failed_labels) != 1 else ''} fallaron."
                )
        if raw_text:
            details.append("Texto manual incluido en el análisis.")
        if image_data_url:
            if image_source:
                details.append(f"Imagen usada: `{image_source}`.")
            details.append("La imagen se aplicará al guardar la receta.")
        elif upload_paths:
            details.append("No se detectó imagen utilizable en los archivos.")
        if failed_labels:
            details.append("Errores: " + " | ".join(failed_labels[:3]))
            if len(failed_labels) > 3:
                details.append(f"(+{len(failed_labels) - 3} errores más)")

        return (
            form_values["name"],
            form_values["tags_text"],
            form_values["total_time"],
            form_values["persons"],
            image_data_url,
            form_values["ingredients_text"],
            form_values["steps_text"],
            gr.update(value=" ".join(details), visible=True),
            gr.update(value=" ".join(details), visible=True),
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Recipe import failed: %s", exc)
        return (
            *no_change[:-2],
            gr.update(value=f"❌ No se pudo importar la receta: {exc}", visible=True),
            gr.update(value=f"❌ No se pudo importar la receta: {exc}", visible=True),
        )


def _open_recipe_import_modal():
    return (
        gr.update(visible=True),
        gr.update(visible=True),
        gr.update(value="", visible=False),
        gr.update(value="", visible=False),
        [],
    )


def _close_recipe_import_modal():
    return (
        gr.update(visible=False),
        gr.update(visible=False),
        gr.update(value="", visible=False),
        gr.update(value="", visible=False),
        [],
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
    if current_slug_raw == NEW_RECIPE_SENTINEL:
        return (
            gr.update(),
            gr.update(value="", visible=False),
            gr.update(value=_bool_state(next_value)),
        )

    payload_slug = str(payload.get("slug") or "").strip()
    resolved_slug = payload_slug or current_slug_raw
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
                recipe_import_open_btn = gr.Button(
                    "Upload",
                    visible=False,
                    variant="secondary",
                    elem_id="recipe-import-open-btn",
                )
                edit_btn = gr.Button(
                    EDIT_TOGGLE_BUTTON_LABEL,
                    visible=False,
                    variant="secondary",
                    elem_id="the-list-card-edit-btn",
                )
                with gr.Column(
                    visible=False,
                    scale=0,
                    min_width=0,
                    elem_id="recipe-download-menu",
                ) as download_menu:
                    gr.Button(
                        EDIT_TOGGLE_BUTTON_LABEL,
                        variant="secondary",
                        elem_id="the-list-download-menu-btn",
                    )
                    with gr.Column(elem_id="recipe-download-options"):
                        download_json_btn = gr.Button(
                            "JSON",
                            variant="secondary",
                            elem_id="the-list-download-json-btn",
                        )
                        download_html_btn = gr.Button(
                            "HTML",
                            variant="secondary",
                            elem_id="the-list-download-html-btn",
                        )
                        download_pdf_btn = gr.Button(
                            "PDF",
                            variant="secondary",
                            elem_id="the-list-download-pdf-btn",
                        )
                delete_btn = gr.Button(
                    EDIT_TOGGLE_BUTTON_LABEL,
                    visible=False,
                    variant="secondary",
                    elem_id="the-list-delete-btn",
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

            recipe_import_modal_backdrop = gr.Button(
                "",
                visible=False,
                elem_id="recipe-import-modal-backdrop",
                variant="secondary",
            )
            recipe_import_remove_index = gr.Textbox(
                value="",
                visible=False,
                interactive=True,
                elem_id="recipe-import-remove-index",
            )
            recipe_import_remove_trigger = gr.Button(
                "_remove_recipe_import_file",
                visible=False,
                elem_id="recipe-import-remove-trigger",
            )
            recipe_import_files_state = gr.State([])
            with gr.Column(visible=False, elem_id="recipe-import-modal") as recipe_import_modal:
                with gr.Row(elem_id="recipe-import-modal-header"):
                    gr.Markdown("**Importar receta (archivo y/o texto)**", elem_id="recipe-import-modal-title")
                    recipe_import_close_btn = gr.Button(
                        "x",
                        variant="secondary",
                        elem_id="recipe-import-modal-close-btn",
                        scale=0,
                        min_width=40,
                    )
                gr.Markdown(
                    "Sube un archivo, pega texto, o combina ambos. Submit analizará las dos entradas juntas.",
                    elem_id="recipe-import-modal-help",
                )
                with gr.Group(elem_id="recipe-import-group"):
                    recipe_import_file = gr.UploadButton(
                        "Subir archivo",
                        file_types=[
                            ".html", ".htm", ".docx", ".doc", ".pdf", ".txt",
                            ".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp",
                        ],
                        file_count="multiple",
                        variant="secondary",
                        elem_id="recipe-import-file-btn",
                    )
                    recipe_import_file_list = gr.HTML(
                        value="",
                        visible=False,
                        elem_id="recipe-import-file-list",
                    )
                    recipe_import_text = gr.Textbox(
                        label="Texto (opcional)",
                        lines=6,
                        placeholder="Pega texto de la receta aquí (puede combinarse con el archivo).",
                        elem_id="recipe-import-text",
                    )
                    recipe_import_modal_status = gr.Markdown(
                        value="",
                        visible=False,
                        elem_id="recipe-import-modal-status",
                    )
                    with gr.Row(elem_id="recipe-import-submit-row"):
                        recipe_import_submit_btn = gr.Button(
                            "Submit",
                            variant="primary",
                            elem_id="recipe-import-submit-btn",
                        )

            current_slug = gr.Textbox(value="", visible=False, interactive=False, elem_id="the-list-current-slug")
            current_name = gr.Textbox(value="", visible=False, interactive=False, elem_id="the-list-current-name")
            current_bucket = gr.Textbox(
                value="",
                visible=False,
                interactive=False,
                elem_id="the-list-current-bucket",
            )
            current_tags = gr.Textbox(value="", visible=False, interactive=False, elem_id="the-list-current-tags")
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
                value=_bool_state(True),
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
            recipe_download_payload = gr.Textbox(
                value="",
                visible=False,
                interactive=False,
                elem_id="recipe-download-payload",
            )

        page_state_outputs = [
            title_md,
            edit_btn,
            download_menu,
            delete_btn,
            recipe_verified_state,
            page_status,
            detail_html,
            detail_markdown,
            card_proposal_shell,
            card_proposal_help,
            card_proposal_name,
            card_proposal_bucket,
            card_proposal_tags,
            card_proposal_total_time,
            card_proposal_persons,
            card_proposal_image_data,
            edit_ingredients_input,
            edit_steps_input,
            current_slug,
            current_name,
            current_bucket,
            current_tags,
            current_total_time,
            current_persons,
            image_route_state,
            card_proposal_status,
            card_proposal_image,
            recipe_import_open_btn,
            recipe_import_modal_backdrop,
            recipe_import_modal,
            recipe_import_modal_status,
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

        delete_btn.click(
            timed_page_load("/receta", _delete_recipe_from_detail, label="delete_recipe_from_detail"),
            inputs=[current_slug],
            outputs=page_state_outputs,
            show_progress=False,
        )

        download_json_btn.click(
            timed_page_load("/receta", _download_recipe_json, label="download_recipe_json"),
            inputs=[current_slug],
            outputs=[recipe_download_payload, page_status],
            show_progress=False,
        )
        download_html_btn.click(
            timed_page_load("/receta", _download_recipe_html, label="download_recipe_html"),
            inputs=[current_slug],
            outputs=[recipe_download_payload, page_status],
            show_progress=False,
        )
        download_pdf_btn.click(
            timed_page_load("/receta", _download_recipe_pdf, label="download_recipe_pdf"),
            inputs=[current_slug],
            outputs=[recipe_download_payload, page_status],
            show_progress=False,
        )

        recipe_import_open_btn.click(
            timed_page_load("/receta", _open_recipe_import_modal, label="open_recipe_import_modal"),
            outputs=[
                recipe_import_modal_backdrop,
                recipe_import_modal,
                recipe_import_modal_status,
                recipe_import_file_list,
                recipe_import_files_state,
            ],
            show_progress=False,
        )
        recipe_import_close_btn.click(
            timed_page_load("/receta", _close_recipe_import_modal, label="close_recipe_import_modal"),
            outputs=[
                recipe_import_modal_backdrop,
                recipe_import_modal,
                recipe_import_modal_status,
                recipe_import_file_list,
                recipe_import_files_state,
            ],
            show_progress=False,
        )
        recipe_import_modal_backdrop.click(
            timed_page_load("/receta", _close_recipe_import_modal, label="close_recipe_import_modal_backdrop"),
            outputs=[
                recipe_import_modal_backdrop,
                recipe_import_modal,
                recipe_import_modal_status,
                recipe_import_file_list,
                recipe_import_files_state,
            ],
            show_progress=False,
        )
        recipe_import_file.upload(
            timed_page_load("/receta", _append_recipe_import_file_selection, label="append_recipe_import_file_selection"),
            inputs=[recipe_import_files_state, recipe_import_file],
            outputs=[recipe_import_files_state, recipe_import_file_list, recipe_import_modal_status],
            show_progress=False,
        )
        recipe_import_remove_trigger.click(
            timed_page_load("/receta", _remove_recipe_import_file_selection, label="remove_recipe_import_file_selection"),
            inputs=[recipe_import_files_state, recipe_import_remove_index],
            outputs=[recipe_import_files_state, recipe_import_file_list, recipe_import_modal_status],
            show_progress=False,
        )

        submit_btn.click(
            timed_page_load("/receta", _save_recipe_edits, label="save_recipe_edits"),
            inputs=[
                current_slug,
                card_proposal_name,
                card_proposal_bucket,
                card_proposal_tags,
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

        recipe_import_submit_btn.click(
            timed_page_load("/receta", _import_recipe_into_editor, label="import_recipe_into_editor"),
            inputs=[recipe_import_files_state, recipe_import_text],
            outputs=[
                card_proposal_name,
                card_proposal_tags,
                card_proposal_total_time,
                card_proposal_persons,
                card_proposal_image_data,
                edit_ingredients_input,
                edit_steps_input,
                card_proposal_status,
                recipe_import_modal_status,
            ],
            show_progress=True,
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
