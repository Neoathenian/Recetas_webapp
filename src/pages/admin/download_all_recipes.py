from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import logging
import os
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Dict, List, Tuple
from uuid import uuid4

import gradio as gr

from src.gcs_storage import get_bucket, storage_client
from src.pages.recetas_display.app_people_display import (
    _recipe_export_html_document,
    _write_recipe_pdf_export,
)
from src.pages.recetas_list.core_the_list import (
    RECIPES_PREFIX,
    _recipe_from_payload,
    _recipe_slug_from_blob_name,
    _slugify,
)

logger = logging.getLogger(__name__)


def _resolve_bulk_export_workers() -> int:
    raw_value = str(os.getenv("RECETAS_BULK_EXPORT_WORKERS", "24")).strip()
    try:
        parsed = int(raw_value)
    except (TypeError, ValueError):
        parsed = 24
    return max(1, min(128, parsed))


def _list_recipe_blobs(*, client=None, bucket=None) -> List[str]:
    if client is None:
        client = storage_client()
    if bucket is None:
        bucket = client.bucket(get_bucket().name)
    prefix = f"{RECIPES_PREFIX}/"
    blob_names = sorted(
        (
            str(getattr(blob, "name", "") or "").strip()
            for blob in client.list_blobs(bucket, prefix=prefix)
        ),
        key=str.lower,
    )
    return [name for name in blob_names if name.endswith(".json")]


def _download_recipe_blob_bytes(*, blob_name: str, client, bucket) -> bytes:
    return bucket.blob(blob_name).download_as_bytes(client=client)


def _download_recipe_blob_bytes_parallel(
    *,
    blob_names: List[str],
    client,
    bucket,
    max_workers: int,
):
    if not blob_names:
        return
    worker_count = max(1, min(max_workers, len(blob_names)))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        future_to_blob = {
            executor.submit(
                _download_recipe_blob_bytes,
                blob_name=blob_name,
                client=client,
                bucket=bucket,
            ): blob_name
            for blob_name in blob_names
        }
        for future in as_completed(future_to_blob):
            blob_name = future_to_blob[future]
            try:
                yield blob_name, future.result(), None
            except Exception as exc:  # noqa: BLE001
                yield blob_name, None, exc


def _parse_recipe_payload(blob_name: str, raw_bytes: bytes) -> Dict[str, object] | None:
    try:
        payload = json.loads(raw_bytes.decode("utf-8"))
    except FileNotFoundError:
        return None
    except json.JSONDecodeError:
        logger.warning("Skipping recipe blob `%s`: invalid JSON.", blob_name)
        return None
    except Exception:  # pragma: no cover
        logger.exception("Failed to read recipe blob `%s`.", blob_name)
        return None

    if not isinstance(payload, dict):
        logger.warning("Skipping recipe blob `%s`: JSON payload must be an object.", blob_name)
        return None
    return payload


def _recipe_slug(blob_name: str, payload: Dict[str, object]) -> str:
    slug = _slugify(str(payload.get("slug") or _recipe_slug_from_blob_name(blob_name)))
    return slug or "receta"


def _recipe_zip_entry_name(slug: str, extension: str) -> str:
    safe_slug = _slugify(slug or "receta")
    safe_ext = str(extension or "").strip().lstrip(".").lower() or "txt"
    return f"{safe_slug}.{safe_ext}"


def _dedupe_zip_entry_name(
    entry_name: str,
    seen_entry_names: set[str],
) -> str:
    if entry_name not in seen_entry_names:
        seen_entry_names.add(entry_name)
        return entry_name

    stem, dot, ext = entry_name.partition(".")
    suffix = 2
    while True:
        candidate = f"{stem}-{suffix}.{ext}" if dot else f"{entry_name}-{suffix}"
        if candidate not in seen_entry_names:
            seen_entry_names.add(candidate)
            return candidate
        suffix += 1


def _recipe_html_bytes(slug: str, payload: Dict[str, object]) -> bytes:
    recipe = _recipe_from_payload(payload, slug_hint=slug)
    return _recipe_export_html_document(recipe).encode("utf-8")


def _recipe_pdf_bytes(slug: str, payload: Dict[str, object]) -> bytes:
    recipe = _recipe_from_payload(payload, slug_hint=slug)
    with tempfile.TemporaryDirectory(prefix="admin-recetas-pdf-") as temp_dir:
        pdf_path = Path(temp_dir) / f"{_slugify(slug)}.pdf"
        _write_recipe_pdf_export(pdf_path, recipe)
        return pdf_path.read_bytes()


def _export_all_recipes_zip(
    export_format: str,
    *,
    progress: gr.Progress | None = None,
) -> Tuple[Path, int, int]:
    normalized_format = str(export_format or "").strip().lower()
    if normalized_format not in {"json", "html", "pdf"}:
        raise ValueError("Formato de exportación no soportado.")

    client = storage_client()
    bucket = client.bucket(get_bucket().name)
    recipe_blobs = _list_recipe_blobs(client=client, bucket=bucket)
    if not recipe_blobs:
        raise ValueError("No hay recetas disponibles para exportar.")
    if progress is not None:
        progress(0.02, desc=f"Preparando exportación {normalized_format.upper()}...")

    zip_path = Path(tempfile.gettempdir()) / f"recetas-{normalized_format}-{uuid4().hex}.zip"
    exported_count = 0
    skipped_count = 0
    seen_entry_names: set[str] = set()

    # JSON bulk export should prioritize speed over max compression ratio.
    zip_kwargs = {
        "compression": zipfile.ZIP_STORED if normalized_format == "json" else zipfile.ZIP_DEFLATED,
    }
    if zip_kwargs["compression"] != zipfile.ZIP_STORED:
        zip_kwargs["compresslevel"] = 1

    with zipfile.ZipFile(zip_path, "w", **zip_kwargs) as archive:
        workers = _resolve_bulk_export_workers()
        total_blobs = len(recipe_blobs)
        processed_blobs = 0

        for blob_name, raw_bytes, download_exc in _download_recipe_blob_bytes_parallel(
            blob_names=recipe_blobs,
            client=client,
            bucket=bucket,
            max_workers=workers,
        ):
            processed_blobs += 1
            if download_exc is not None:
                logger.warning("Failed downloading recipe blob `%s`: %s", blob_name, download_exc)
                skipped_count += 1
                continue

            slug = _slugify(_recipe_slug_from_blob_name(blob_name))
            file_bytes = raw_bytes or b""
            if normalized_format != "json":
                payload = _parse_recipe_payload(blob_name, raw_bytes)
                if payload is None:
                    skipped_count += 1
                    continue

                slug = _recipe_slug(blob_name, payload)
                try:
                    if normalized_format == "html":
                        file_bytes = _recipe_html_bytes(slug, payload)
                    else:
                        file_bytes = _recipe_pdf_bytes(slug, payload)
                except Exception:  # pragma: no cover
                    logger.exception(
                        "Failed building %s export for recipe `%s` from `%s`.",
                        normalized_format,
                        slug,
                        blob_name,
                    )
                    skipped_count += 1
                    continue

            entry_name = _recipe_zip_entry_name(slug, normalized_format)
            unique_entry_name = _dedupe_zip_entry_name(entry_name, seen_entry_names)
            archive.writestr(unique_entry_name, file_bytes)
            exported_count += 1

            if processed_blobs % 25 == 0 or processed_blobs == total_blobs:
                if progress is not None and total_blobs > 0:
                    progress(
                        processed_blobs / total_blobs,
                        desc=(
                            f"Procesando {normalized_format.upper()} "
                            f"{processed_blobs}/{total_blobs}..."
                        ),
                    )
                logger.info(
                    "recipes_export.progress format=%s processed=%s total=%s exported=%s skipped=%s",
                    normalized_format,
                    processed_blobs,
                    total_blobs,
                    exported_count,
                    skipped_count,
                )

    if exported_count <= 0:
        raise ValueError("No se pudo exportar ninguna receta.")
    return zip_path, exported_count, skipped_count


def _handle_download_all_recipes_zip(
    export_format: str,
    progress=gr.Progress(track_tqdm=False),
):
    start = time.perf_counter()
    normalized_format = str(export_format or "").strip().lower()
    label = normalized_format.upper()
    progress(0, desc=f"Listando recetas para ZIP {label}...")
    try:
        zip_path, exported_count, skipped_count = _export_all_recipes_zip(
            normalized_format,
            progress=progress,
        )
    except ValueError as exc:
        return gr.update(), f"❌ {exc}"
    except Exception as exc:  # pragma: no cover
        logger.exception("Failed exporting all recipes as %s ZIP.", normalized_format)
        return gr.update(), f"❌ Error al exportar recetas ({label}): {exc}"

    progress(1.0, desc=f"ZIP {label} listo.")
    elapsed_seconds = max(0.0, time.perf_counter() - start)
    message = f"📦 ZIP {label} preparado con {exported_count} receta{'s' if exported_count != 1 else ''}."
    if skipped_count > 0:
        message += f" ⚠️ Se omitieron {skipped_count} receta{'s' if skipped_count != 1 else ''}."
    message += f" ({elapsed_seconds:.1f}s)"
    return str(zip_path), message


def handle_download_all_recipes_json_zip(
    progress=gr.Progress(track_tqdm=False),
):
    return _handle_download_all_recipes_zip("json", progress=progress)


def handle_download_all_recipes_html_zip(
    progress=gr.Progress(track_tqdm=False),
):
    return _handle_download_all_recipes_zip("html", progress=progress)


def handle_download_all_recipes_pdf_zip(
    progress=gr.Progress(track_tqdm=False),
):
    return _handle_download_all_recipes_zip("pdf", progress=progress)
