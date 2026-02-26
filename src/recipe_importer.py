from __future__ import annotations

import base64
import html as html_lib
import io
import json
import logging
import mimetypes
import re
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Dict
from urllib.parse import unquote, urlparse

import requests
from PIL import Image, ImageOps

from src.LLMs_funcs import recipe_file_to_webapp_json, recipe_text_to_webapp_json

logger = logging.getLogger(__name__)

TEXT_SOURCE_EXTENSIONS = {".html", ".htm", ".docx", ".doc", ".pdf", ".txt"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
SUPPORTED_IMPORT_EXTENSIONS = TEXT_SOURCE_EXTENSIONS | IMAGE_EXTENSIONS
DEFAULT_CARD_COLOR = "rgb(118, 161, 146)"


def recipe_payload_is_empty(payload: Dict[str, Any] | None) -> bool:
    if not isinstance(payload, dict):
        return True
    name = str(payload.get("Name") or "").strip().lower()
    ingredients = payload.get("Ingredients")
    steps = payload.get("Steps")
    has_ingredients = False
    if isinstance(ingredients, dict):
        for raw_key, raw_value in ingredients.items():
            key_text = str(raw_key or "").strip()
            value_text = str(raw_value or "").strip()
            if key_text or value_text:
                has_ingredients = True
                break
    has_steps = False
    if isinstance(steps, list):
        for raw_step in steps:
            if str(raw_step or "").strip():
                has_steps = True
                break

    # Title/tags-only payloads are still unusable recipes.
    if not has_ingredients and not has_steps:
        return True

    return (
        name in {"", "receta"}
        and not has_ingredients
        and not has_steps
    )


def recipe_payload_to_form_values(payload: Dict[str, Any]) -> Dict[str, str]:
    ingredients = payload.get("Ingredients")
    steps = payload.get("Steps")
    tags = payload.get("Tags")
    tools = payload.get("Tools")

    ingredient_lines: list[str] = []
    if isinstance(ingredients, dict):
        for raw_name, raw_amount in ingredients.items():
            ingredient = str(raw_name or "").strip()
            amount = str(raw_amount or "").strip()
            if ingredient and amount:
                ingredient_lines.append(f"{amount} | {ingredient}")
            elif ingredient:
                ingredient_lines.append(ingredient)
            elif amount:
                ingredient_lines.append(f"{amount} |")

    step_lines: list[str] = []
    if isinstance(steps, list):
        for item in steps:
            text = str(item or "").strip()
            if text:
                step_lines.append(text)

    def _csv_list(value: object) -> str:
        if not isinstance(value, list):
            return ""
        out: list[str] = []
        seen: set[str] = set()
        for item in value:
            text = str(item or "").strip().lower()
            if not text or text in seen:
                continue
            seen.add(text)
            out.append(text)
        return ", ".join(out)

    return {
        "name": str(payload.get("Name") or "Receta").strip() or "Receta",
        "tags_text": _csv_list(tags),
        "tools_text": _csv_list(tools),
        "total_time": str(payload.get("Total time") or "No especificado").strip() or "No especificado",
        "persons": str(payload.get("Nºpersonas") or "No especificado").strip() or "No especificado",
        "ingredients_text": "\n".join(ingredient_lines),
        "steps_text": "\n".join(step_lines),
    }


def import_recipe_from_path_or_text(
    upload_path: str | Path | None,
    raw_text: str | None,
    *,
    force_english: bool = False,
) -> Dict[str, Any]:
    path_obj = Path(str(upload_path)).expanduser() if str(upload_path or "").strip() else None
    user_text = str(raw_text or "").strip()

    source_label = "texto manual"
    suffix = ""
    extracted_text = ""

    if path_obj is not None:
        source_label = path_obj.name
        suffix = path_obj.suffix.lower()
        if not path_obj.exists() or not path_obj.is_file():
            raise ValueError(f"No se encontró el archivo importado: {path_obj}")
        if suffix not in SUPPORTED_IMPORT_EXTENSIONS:
            supported = ", ".join(sorted(SUPPORTED_IMPORT_EXTENSIONS))
            raise ValueError(f"Extensión no soportada (`{suffix}`). Permitidas: {supported}")
        if suffix in TEXT_SOURCE_EXTENSIONS:
            extracted_text = _extract_source_text(path_obj)

    payload: Dict[str, Any]
    should_use_gemini_file_ocr = False
    if path_obj is not None:
        # Use Gemini file OCR/reading for image uploads directly, and for documents when local extraction is empty.
        should_use_gemini_file_ocr = suffix in IMAGE_EXTENSIONS or (suffix in TEXT_SOURCE_EXTENSIONS and not extracted_text.strip())

    if should_use_gemini_file_ocr and path_obj is not None:
        mime_type = _guess_mime_type(path_obj)
        context_bits = []
        if user_text:
            context_bits.append(f"USER_PROVIDED_TEXT:\n{user_text}")
        if extracted_text:
            context_bits.append(f"LOCAL_EXTRACTED_TEXT:\n{extracted_text}")
        context_text = "\n\n".join(context_bits).strip()
        payload = recipe_file_to_webapp_json(
            path_obj.read_bytes(),
            mime_type,
            context_text=context_text,
            force_english=force_english,
            default_card_color=DEFAULT_CARD_COLOR,
        )
    else:
        if not extracted_text and not user_text:
            raise ValueError(
                "No se pudo extraer texto del archivo. Sube un archivo compatible o pega el texto de la receta."
            )
        text_sections: list[str] = []
        if user_text:
            text_sections.append(f"USER_PROVIDED_TEXT:\n{user_text}")
        if extracted_text:
            text_sections.append(extracted_text)
        llm_input = "\n\n".join(text_sections).strip()
        payload = recipe_text_to_webapp_json(
            llm_input,
            force_english=force_english,
            default_card_color=DEFAULT_CARD_COLOR,
        )

    # HTML retry/fallback when the LLM returns a default/empty object.
    if recipe_payload_is_empty(payload) and path_obj is not None and path_obj.suffix.lower() in {".html", ".htm"}:
        fallback = _fallback_payload_from_html_jsonld(path_obj)
        if fallback is not None:
            retry_input = (
                "STRUCTURED_RECIPE_FALLBACK:\n"
                + json.dumps(fallback, ensure_ascii=False, indent=2)
                + ("\n\nUSER_PROVIDED_TEXT:\n" + user_text if user_text else "")
            )
            retry_payload = recipe_text_to_webapp_json(
                retry_input,
                force_english=force_english,
                default_card_color=DEFAULT_CARD_COLOR,
            )
            payload = retry_payload if not recipe_payload_is_empty(retry_payload) else fallback

    if recipe_payload_is_empty(payload) and path_obj is not None and path_obj.suffix.lower() in {".pdf", ".doc", ".docx"}:
        try:
            retry_context_bits: list[str] = []
            if user_text:
                retry_context_bits.append(f"USER_PROVIDED_TEXT:\n{user_text}")
            if extracted_text:
                retry_context_bits.append(f"LOCAL_EXTRACTED_TEXT:\n{extracted_text}")
            retry_context = "\n\n".join(retry_context_bits).strip()
            retry_file_payload = recipe_file_to_webapp_json(
                path_obj.read_bytes(),
                _guess_mime_type(path_obj),
                context_text=retry_context,
                force_english=force_english,
                default_card_color=DEFAULT_CARD_COLOR,
            )
            if not recipe_payload_is_empty(retry_file_payload):
                payload = retry_file_payload
        except Exception as exc:  # noqa: BLE001
            logger.warning("Gemini file retry failed for %s: %s", path_obj, exc)

    if recipe_payload_is_empty(payload):
        plain_text_input = "\n\n".join(
            part for part in [user_text, extracted_text] if str(part or "").strip()
        ).strip()
        fallback_plain = _fallback_payload_from_plain_text(path_obj, plain_text_input)
        if fallback_plain is not None:
            payload = fallback_plain

    if recipe_payload_is_empty(payload):
        raise ValueError("La IA devolvió una receta vacía (sin nombre/ingredientes/pasos).")

    image_data_url = ""
    image_source = ""
    if path_obj is not None:
        image_bytes, image_source = _extract_image_bytes(path_obj)
        if image_bytes:
            try:
                image_data_url = _image_bytes_to_card_data_url(image_bytes)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Skipping unsupported/invalid extracted image for %s: %s", path_obj, exc)
                image_data_url = ""
                image_source = ""

    return {
        "payload": payload,
        "image_data_url": image_data_url,
        "source_label": source_label,
        "image_source": image_source,
    }


def _guess_mime_type(path: Path) -> str:
    guessed, _ = mimetypes.guess_type(str(path))
    if guessed:
        return guessed
    suffix = path.suffix.lower()
    if suffix == ".docx":
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    if suffix == ".doc":
        return "application/msword"
    if suffix == ".pdf":
        return "application/pdf"
    if suffix in {".html", ".htm"}:
        return "text/html"
    if suffix == ".txt":
        return "text/plain"
    return "application/octet-stream"


def _read_text_file(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def _clean_inline_text(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", str(value or ""), flags=re.IGNORECASE)
    text = html_lib.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _strip_html_text(raw_html: str) -> str:
    no_script = re.sub(r"<script[\s\S]*?</script>", " ", raw_html, flags=re.IGNORECASE)
    no_style = re.sub(r"<style[\s\S]*?</style>", " ", no_script, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", no_style)
    text = html_lib.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _extract_recipe_jsonld(raw_html: str) -> list[dict[str, Any]]:
    blocks = re.findall(
        r"<script[^>]*type=[\\\"']application/ld\+json[\\\"'][^>]*>([\s\S]*?)</script>",
        raw_html,
        flags=re.IGNORECASE,
    )
    recipes: list[dict[str, Any]] = []
    for block in blocks:
        candidate = (block or "").strip()
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
        except Exception:
            continue

        stack: list[Any] = [parsed]
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack.extend(node)
                continue
            if not isinstance(node, dict):
                continue
            node_type = str(node.get("@type") or "").lower()
            if node_type == "recipe":
                recipes.append(node)
                continue
            graph = node.get("@graph")
            if isinstance(graph, list):
                stack.extend(graph)
    return recipes


def _extract_jsonld_steps(value: Any) -> list[str]:
    if isinstance(value, str):
        text = _clean_inline_text(value)
        return [text] if text else []
    if not isinstance(value, list):
        return []
    steps: list[str] = []
    for item in value:
        if isinstance(item, str):
            text = _clean_inline_text(item)
        elif isinstance(item, dict):
            text = _clean_inline_text(item.get("text") or item.get("name") or "")
        else:
            text = ""
        if text:
            steps.append(text)
    return steps


def _extract_jsonld_ingredients(value: Any) -> list[str]:
    if isinstance(value, list):
        out: list[str] = []
        for row in value:
            text = _clean_inline_text(row)
            if text:
                out.append(text)
        return out
    text = _clean_inline_text(value) if isinstance(value, str) else ""
    return [text] if text else []


def _best_recipe_jsonld_node(nodes: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not nodes:
        return None

    def _score(node: dict[str, Any]) -> tuple[int, int, int]:
        return (
            len(_extract_jsonld_ingredients(node.get("recipeIngredient"))),
            len(_extract_jsonld_steps(node.get("recipeInstructions"))),
            1 if _clean_inline_text(node.get("name") or "") else 0,
        )

    return max(nodes, key=_score)


def _compact_jsonld_recipe(node: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": _clean_inline_text(node.get("name") or ""),
        "description": _clean_inline_text(node.get("description") or ""),
        "prepTime": _clean_inline_text(node.get("prepTime") or ""),
        "cookTime": _clean_inline_text(node.get("cookTime") or ""),
        "totalTime": _clean_inline_text(node.get("totalTime") or ""),
        "recipeYield": _clean_inline_text(node.get("recipeYield") or ""),
        "recipeCategory": node.get("recipeCategory"),
        "keywords": _clean_inline_text(node.get("keywords") or ""),
        "recipeIngredient": _extract_jsonld_ingredients(node.get("recipeIngredient")),
        "recipeInstructions": _extract_jsonld_steps(node.get("recipeInstructions")),
    }


def _looks_like_rtf_bytes(data: bytes | None) -> bool:
    if not data:
        return False
    return data.lstrip().startswith(b"{\\rtf")


def _rtf_to_text(raw_rtf: str) -> str:
    # Minimal RTF deformatter suitable for legacy recipe documents exported as .doc/.rtf.
    destinations = {
        "fonttbl",
        "colortbl",
        "datastore",
        "themedata",
        "stylesheet",
        "info",
        "pict",
        "object",
        "fldinst",
        "xmlattrname",
        "xmlattrvalue",
        "xmlclose",
        "xmlname",
        "xmlnstbl",
        "xmlopen",
        "xmlpi",
        "xmlterm",
    }
    specials = {
        "par": "\n",
        "line": "\n",
        "tab": "\t",
        "emdash": "-",
        "endash": "-",
        "bullet": "•",
        "lquote": "'",
        "rquote": "'",
        "ldblquote": '"',
        "rdblquote": '"',
    }

    stack: list[tuple[int, bool]] = []
    ignorable = False
    ucskip = 1
    curskip = 0
    out: list[str] = []
    i = 0
    n = len(raw_rtf)

    while i < n:
        ch = raw_rtf[i]
        if ch == "{":
            stack.append((ucskip, ignorable))
            i += 1
            continue
        if ch == "}":
            if stack:
                ucskip, ignorable = stack.pop()
            i += 1
            continue
        if ch == "\\":
            i += 1
            if i >= n:
                break
            ch = raw_rtf[i]

            if ch in "{}\\":
                if curskip > 0:
                    curskip -= 1
                elif not ignorable:
                    out.append(ch)
                i += 1
                continue
            if ch == "*":
                ignorable = True
                i += 1
                continue
            if ch == "'" and i + 2 < n:
                hex_pair = raw_rtf[i + 1 : i + 3]
                try:
                    decoded = bytes.fromhex(hex_pair).decode("cp1252", errors="ignore")
                except Exception:
                    decoded = ""
                if curskip > 0:
                    curskip = max(0, curskip - 1)
                elif not ignorable and decoded:
                    out.append(decoded)
                i += 3
                continue
            if ch.isalpha():
                start = i
                while i < n and raw_rtf[i].isalpha():
                    i += 1
                word = raw_rtf[start:i]
                sign = ""
                if i < n and raw_rtf[i] in "+-":
                    sign = raw_rtf[i]
                    i += 1
                num_start = i
                while i < n and raw_rtf[i].isdigit():
                    i += 1
                arg: int | None = None
                if i > num_start:
                    try:
                        arg = int((sign or "") + raw_rtf[num_start:i])
                    except Exception:
                        arg = None
                if i < n and raw_rtf[i] == " ":
                    i += 1

                if word in destinations:
                    ignorable = True
                elif word == "uc" and arg is not None:
                    ucskip = max(0, arg)
                elif word == "u" and arg is not None:
                    if arg < 0:
                        arg += 0x10000
                    if not ignorable:
                        try:
                            out.append(chr(arg))
                        except ValueError:
                            pass
                    curskip = ucskip
                else:
                    replacement = specials.get(word)
                    if replacement and not ignorable:
                        out.append(replacement)
                continue

            if ch == "~":
                if not ignorable:
                    out.append(" ")
                i += 1
                continue
            if ch in {"-", "_"}:
                if not ignorable:
                    out.append("-" if ch == "-" else "_")
                i += 1
                continue

            i += 1
            continue

        if curskip > 0:
            curskip -= 1
            i += 1
            continue
        if not ignorable:
            out.append(ch)
        i += 1

    text = "".join(out)
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()


def _extract_rtf_text(path: Path) -> str:
    try:
        raw = path.read_text(encoding="latin-1", errors="ignore")
    except Exception:
        logger.exception("Failed reading RTF-like text from %s", path)
        return ""
    if "{\\rtf" not in raw[:256].lower():
        return ""
    return _rtf_to_text(raw)


def _extract_office_text_via_soffice(path: Path) -> str:
    last_error: Exception | None = None
    for binary in ("soffice", "libreoffice"):
        for target in ("txt:Text", "txt"):
            try:
                with tempfile.TemporaryDirectory(prefix="office-prof-") as profile_dir, tempfile.TemporaryDirectory(
                    prefix="office-to-txt-"
                ) as td:
                    outdir = Path(td)
                    cmd = [
                        binary,
                        "--headless",
                        "--nologo",
                        "--nodefault",
                        "--nolockcheck",
                        "--norestore",
                        f"-env:UserInstallation=file://{profile_dir}",
                        "--convert-to",
                        target,
                        "--outdir",
                        str(outdir),
                        str(path),
                    ]
                    cp = subprocess.run(cmd, check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    txt_files = sorted(outdir.glob("*.txt"))
                    if txt_files:
                        text = txt_files[0].read_text(encoding="utf-8", errors="ignore").strip()
                        if text:
                            return text
                    if cp.returncode != 0:
                        last_error = subprocess.CalledProcessError(cp.returncode, cmd, cp.stdout, cp.stderr)
            except FileNotFoundError:
                continue
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                continue

    if last_error is not None:
        logger.warning("Office conversion to text failed for %s: %s", path, last_error)
    return ""


def _extract_docx_text(path: Path) -> str:
    text = ""
    try:
        with zipfile.ZipFile(path) as zf:
            xml = zf.read("word/document.xml").decode("utf-8", errors="ignore")
    except Exception:
        logger.warning("DOCX zip parsing failed for %s; trying office converter fallback", path)
    else:
        xml = xml.replace("</w:p>", "\n")
        text = re.sub(r"<[^>]+>", " ", xml)
        text = html_lib.unescape(text)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n\s*\n+", "\n", text).strip()
        if text:
            return text

    office_text = _extract_office_text_via_soffice(path)
    if office_text:
        return office_text

    try:
        head = path.read_bytes()[:256]
    except Exception:
        head = b""
    if _looks_like_rtf_bytes(head):
        return _extract_rtf_text(path)
    return ""


def _extract_doc_text_via_libreoffice(path: Path) -> str:
    try:
        head = path.read_bytes()[:512]
    except Exception:
        head = b""

    if _looks_like_rtf_bytes(head):
        rtf_text = _extract_rtf_text(path)
        if rtf_text:
            return rtf_text

    office_text = _extract_office_text_via_soffice(path)
    if office_text:
        return office_text

    return _extract_rtf_text(path)


def _extract_pdf_text(path: Path) -> str:
    try:
        with tempfile.TemporaryDirectory(prefix="pdf-to-txt-") as td:
            out_txt = Path(td) / "out.txt"
            cmd = ["pdftotext", "-layout", str(path), str(out_txt)]
            subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if not out_txt.exists():
                return ""
            return out_txt.read_text(encoding="utf-8", errors="ignore").strip()
    except FileNotFoundError:
        logger.warning("pdftotext not available")
        return ""
    except Exception:
        logger.exception("Failed extracting PDF text from %s", path)
        return ""


def _extract_source_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".html", ".htm"}:
        raw_html = _read_text_file(path)
        best = _best_recipe_jsonld_node(_extract_recipe_jsonld(raw_html))
        if best is not None:
            compact = _compact_jsonld_recipe(best)
            return "RECIPE_JSONLD_PRIMARY:\n" + json.dumps(compact, ensure_ascii=False, indent=2)
        return "PAGE_TEXT:\n" + _strip_html_text(raw_html)[:120000]
    if suffix == ".docx":
        return _extract_docx_text(path)
    if suffix == ".doc":
        return _extract_doc_text_via_libreoffice(path)
    if suffix == ".pdf":
        return _extract_pdf_text(path)
    if suffix == ".txt":
        return _read_text_file(path)
    return ""


def _download_image(url: str, timeout: int = 20) -> bytes | None:
    try:
        response = requests.get(url, timeout=timeout)
        if response.status_code != 200 or not response.content:
            return None
        return response.content
    except Exception:
        return None


def _first_docx_image(path: Path) -> bytes | None:
    try:
        with zipfile.ZipFile(path) as zf:
            media = sorted(
                name
                for name in zf.namelist()
                if name.lower().startswith("word/media/") and not name.endswith("/")
            )
            if not media:
                return None
            for name in media:
                ext = Path(name).suffix.lower()
                # Skip vector formats like WMF/EMF that Pillow often cannot decode here.
                if ext not in IMAGE_EXTENSIONS:
                    continue
                try:
                    data = zf.read(name)
                except Exception:
                    continue
                if not data:
                    continue
                try:
                    with Image.open(io.BytesIO(data)) as img:
                        img.verify()
                except Exception:
                    continue
                return data
            return None
    except Exception:
        return None


def _first_pdf_image(path: Path) -> bytes | None:
    try:
        with tempfile.TemporaryDirectory(prefix="pdf-images-") as td:
            prefix = str(Path(td) / "img")
            cmd = ["pdfimages", "-png", str(path), prefix]
            subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            pngs = sorted(Path(td).glob("img-*.png"))
            if not pngs:
                return None
            return pngs[0].read_bytes()
    except FileNotFoundError:
        logger.warning("pdfimages not available")
        return None
    except Exception:
        return None


def _first_doc_image_via_libreoffice(path: Path) -> bytes | None:
    try:
        with tempfile.TemporaryDirectory(prefix="doc-images-") as td:
            outdir = Path(td)
            cmd = [
                "libreoffice",
                "--headless",
                "--convert-to",
                "pdf",
                "--outdir",
                str(outdir),
                str(path),
            ]
            subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            pdfs = sorted(outdir.glob("*.pdf"))
            if not pdfs:
                return None
            return _first_pdf_image(pdfs[0])
    except FileNotFoundError:
        return None
    except Exception:
        return None


def _html_image_candidates(raw_html: str) -> list[str]:
    candidates: list[str] = []
    for value in re.findall(
        r"<meta[^>]+property=[\\\"']og:image[\\\"'][^>]+content=[\\\"']([^\\\"']+)[\\\"']",
        raw_html,
        flags=re.IGNORECASE,
    ):
        candidates.append(value.strip())
    for node in _extract_recipe_jsonld(raw_html):
        image_field = node.get("image")
        if isinstance(image_field, str):
            candidates.append(image_field.strip())
        elif isinstance(image_field, list):
            for item in image_field:
                if isinstance(item, str):
                    candidates.append(item.strip())
    for value in re.findall(
        r"<img[^>]+src=[\\\"']([^\\\"']+)[\\\"']",
        raw_html,
        flags=re.IGNORECASE,
    ):
        candidates.append(value.strip())
    unique: list[str] = []
    seen: set[str] = set()
    for value in candidates:
        if not value or value in seen:
            continue
        seen.add(value)
        unique.append(value)
    return unique


def _load_image_candidate(candidate: str, base_dir: Path) -> bytes | None:
    raw = str(candidate or "").strip()
    if not raw:
        return None
    parsed = urlparse(raw)
    if parsed.scheme in {"http", "https"}:
        return _download_image(raw)
    cleaned = unquote(raw.split("?", 1)[0].split("#", 1)[0]).strip()
    if not cleaned:
        return None
    local_path = (base_dir / cleaned).resolve() if not cleaned.startswith("/") else Path(cleaned)
    if local_path.exists() and local_path.is_file():
        try:
            return local_path.read_bytes()
        except Exception:
            return None
    return None


def _sibling_image(path: Path) -> bytes | None:
    stem = path.stem.lower()
    for ext in IMAGE_EXTENSIONS:
        candidate = path.with_suffix(ext)
        if candidate.exists() and candidate.is_file():
            return candidate.read_bytes()
    for item in sorted(path.parent.glob("*")):
        if not item.is_file() or item.suffix.lower() not in IMAGE_EXTENSIONS:
            continue
        if stem in item.stem.lower():
            return item.read_bytes()
    return None


def _extract_image_bytes(path: Path) -> tuple[bytes | None, str]:
    suffix = path.suffix.lower()
    if suffix in IMAGE_EXTENSIONS:
        try:
            return path.read_bytes(), "source-image"
        except Exception:
            return None, ""
    if suffix in {".html", ".htm"}:
        raw_html = _read_text_file(path)
        for candidate in _html_image_candidates(raw_html):
            data = _load_image_candidate(candidate, path.parent)
            if data:
                return data, candidate
    if suffix == ".docx":
        data = _first_docx_image(path)
        if data:
            return data, "docx-embedded"
    if suffix == ".pdf":
        data = _first_pdf_image(path)
        if data:
            return data, "pdf-extracted"
    if suffix == ".doc":
        data = _first_doc_image_via_libreoffice(path)
        if data:
            return data, "doc-converted-pdf"
    sibling = _sibling_image(path)
    if sibling:
        return sibling, "sibling-image"
    return None, ""


def _image_bytes_to_card_data_url(image_bytes: bytes) -> str:
    with Image.open(io.BytesIO(image_bytes)) as img:
        rgb = img.convert("RGB")
        resampling = getattr(Image, "Resampling", Image).LANCZOS
        fitted = ImageOps.fit(rgb, (360, 270), method=resampling)
        out = io.BytesIO()
        fitted.save(out, format="PNG", optimize=True)
    b64 = base64.b64encode(out.getvalue()).decode("ascii")
    return f"data:image/png;base64,{b64}"


def _parse_iso_duration(duration_text: str) -> str:
    text = str(duration_text or "").strip().upper()
    if not text.startswith("P"):
        return str(duration_text or "").strip() or "No especificado"
    days = int(re.search(r"(\d+)D", text).group(1)) if re.search(r"(\d+)D", text) else 0
    hours = int(re.search(r"(\d+)H", text).group(1)) if re.search(r"(\d+)H", text) else 0
    minutes = int(re.search(r"(\d+)M", text).group(1)) if re.search(r"(\d+)M", text) else 0
    total_hours = days * 24 + hours
    parts: list[str] = []
    if total_hours:
        parts.append(f"{total_hours} h")
    if minutes:
        parts.append(f"{minutes} min")
    return " ".join(parts) if parts else "No especificado"


def _parse_persons(value: Any) -> str:
    text = _clean_inline_text(value or "")
    if not text:
        return "No especificado"
    match = re.search(r"\d+", text)
    return match.group(0) if match else text


def _split_amount_ingredient(raw: str) -> tuple[str, str]:
    text = _clean_inline_text(raw)
    if not text:
        return "", ""
    match = re.match(
        r"^([~≈]?[\d]+(?:[.,]\d+)?(?:\s*[/-]\s*[\d]+(?:[.,]\d+)?)?\s*(?:g|kg|ml|l|tsp|tbsp|cup|cups|oz|lb|ud|uds|unid(?:ad(?:es)?)?)?)(?:\s+)(.+)$",
        text,
        flags=re.IGNORECASE,
    )
    if match:
        return match.group(2).strip(), match.group(1).strip()
    return text, ""


def _fallback_payload_from_plain_text(path: Path | None, raw_text: str) -> Dict[str, Any] | None:
    text = str(raw_text or "").replace("\r\n", "\n").replace("\r", "\n")
    if not text.strip():
        return None

    lines = [str(line or "").strip() for line in text.split("\n")]
    ingredient_header_re = re.compile(r"^(ingredientes?|ingredients?)\b", flags=re.IGNORECASE)
    steps_header_re = re.compile(
        r"^(preparaci[oó]n|elaboraci[oó]n|modo de preparaci[oó]n|modo de elaboraci[oó]n|pasos?|instructions?|method)\b",
        flags=re.IGNORECASE,
    )
    stop_section_re = re.compile(r"^(nota|notas|observaciones?|comentarios?)\b", flags=re.IGNORECASE)
    meta_line_re = re.compile(
        r"^(tiempo(?:\s+total|\s+de\s+preparaci[oó]n|\s+de\s+cocci[oó]n)?|raciones?|comensales?|dificultad|porciones?)\b",
        flags=re.IGNORECASE,
    )

    title = ""
    for line in lines[:24]:
        clean = _clean_inline_text(line).strip("“”\"")
        if not clean:
            continue
        if ingredient_header_re.match(clean) or steps_header_re.match(clean):
            continue
        if meta_line_re.match(clean):
            continue
        if clean.lower() in {"private"}:
            continue
        if len(clean) > 160:
            continue
        if re.match(r"^[~≈]?\d|^[¼½¾]", clean):
            continue
        title = re.sub(r"\bPRIVATE\b", "", clean, flags=re.IGNORECASE).strip(" -:")
        if title:
            break
    if not title:
        title = str(path.stem if path else "Receta").replace("_", " ").strip() or "Receta"

    ingredients: Dict[str, str] = {}
    idx = 0
    while idx < len(lines):
        if not ingredient_header_re.match(_clean_inline_text(lines[idx])):
            idx += 1
            continue
        idx += 1
        while idx < len(lines):
            clean = _clean_inline_text(lines[idx])
            if not clean:
                idx += 1
                continue
            if steps_header_re.match(clean) or stop_section_re.match(clean):
                break
            item = re.sub(r"^[\-•·]\s*", "", clean)
            item = re.sub(r"^\d+[.)]\s*", "", item)
            if re.match(r"^\d+\s+(?:[~≈]?\d|[¼½¾])", item):
                item = re.sub(r"^\d+\s+", "", item)
            name, amount = _split_amount_ingredient(item)
            if name:
                key = name
                suffix_num = 2
                while key in ingredients:
                    key = f"{name} ({suffix_num})"
                    suffix_num += 1
                ingredients[key] = amount
            idx += 1
        break

    steps: list[str] = []
    idx = 0
    while idx < len(lines):
        if not steps_header_re.match(_clean_inline_text(lines[idx])):
            idx += 1
            continue
        idx += 1
        current_parts: list[str] = []
        while idx < len(lines):
            clean = _clean_inline_text(lines[idx])
            if not clean:
                if current_parts:
                    step_text = " ".join(current_parts).strip()
                    if step_text:
                        steps.append(step_text)
                    current_parts = []
                idx += 1
                continue
            if stop_section_re.match(clean):
                break
            starts_numbered = bool(re.match(r"^\d+[.)]\s*", clean))
            if starts_numbered and current_parts:
                step_text = " ".join(current_parts).strip()
                if step_text:
                    steps.append(step_text)
                current_parts = []
            line_text = re.sub(r"^\d+[.)]\s*", "", clean)
            line_text = re.sub(r"^[\-•·]\s*", "", line_text)
            if line_text:
                current_parts.append(line_text)
            idx += 1
        if current_parts:
            step_text = " ".join(current_parts).strip()
            if step_text:
                steps.append(step_text)
        break

    # Heuristic fallback for headerless docs (common in old DOC/DOCX recipes): short ingredient lines + one/more method lines.
    if not ingredients and not steps:
        clean_non_empty_lines = []
        for raw_line in lines:
            clean = _clean_inline_text(raw_line)
            if not clean:
                continue
            clean = re.sub(r"\bPRIVATE\b", "", clean, flags=re.IGNORECASE).strip(" -:")
            if not clean:
                continue
            clean_non_empty_lines.append(clean)

        if clean_non_empty_lines:
            title_index = 0
            for idx, line in enumerate(clean_non_empty_lines[:12]):
                if ingredient_header_re.match(line) or steps_header_re.match(line) or meta_line_re.match(line):
                    continue
                if re.match(r"^[~≈]?\d|^[¼½¾]", line):
                    continue
                title_index = idx
                if not title or title.strip().lower() in {"", "receta"}:
                    title = line
                break

            cursor = min(title_index + 1, len(clean_non_empty_lines))

            # Optional subtitle (e.g. "Salsa a la pimienta...") before ingredient list
            if cursor < len(clean_non_empty_lines):
                first_candidate = clean_non_empty_lines[cursor]
                next_line = clean_non_empty_lines[cursor + 1] if cursor + 1 < len(clean_non_empty_lines) else ""
                if (
                    len(first_candidate.split()) <= 10
                    and len(first_candidate) <= 90
                    and next_line
                    and len(next_line.split()) <= 4
                    and not re.search(r"[.!?]$", first_candidate)
                ):
                    if title and title.strip().lower() in {"", "receta"}:
                        title = first_candidate
                    elif title and title.lower().startswith("salsas") and not first_candidate.lower().startswith("ingred"):
                        title = first_candidate
                    cursor += 1

            ingredient_lines: list[str] = []
            step_lines: list[str] = []

            for line in clean_non_empty_lines[cursor:]:
                lower = line.lower()
                looks_like_step = (
                    bool(re.search(r"[.!?]$", line) and len(line.split()) >= 5)
                    or lower.startswith(("se ", "poner", "pon", "añadir", "agregar", "mezclar", "batir", "cocer", "hornear"))
                )
                if looks_like_step:
                    step_lines.append(line)
                    continue
                if step_lines:
                    step_lines.append(line)
                    continue
                if stop_section_re.match(line) or meta_line_re.match(line):
                    continue
                if len(line) <= 90 and len(line.split()) <= 8:
                    ingredient_lines.append(line)
                else:
                    step_lines.append(line)

            for item in ingredient_lines:
                key_name = item
                disambiguator = 2
                while key_name in ingredients:
                    key_name = f"{item} ({disambiguator})"
                    disambiguator += 1
                ingredients[key_name] = ""

            if step_lines:
                merged_step = " ".join(step_lines).strip()
                if merged_step:
                    steps.append(merged_step)

    if not ingredients and not steps:
        return None

    return {
        "Name": title or "Receta",
        "Ingredients": ingredients,
        "Steps": steps,
        "Preparation time": "No especificado",
        "Total time": "No especificado",
        "Nºpersonas": "No especificado",
        "Tags": [],
        "Tools": [],
    }


def _fallback_payload_from_html_jsonld(path: Path) -> Dict[str, Any] | None:
    raw_html = _read_text_file(path)
    best = _best_recipe_jsonld_node(_extract_recipe_jsonld(raw_html))
    if best is None:
        return None

    ingredients: Dict[str, str] = {}
    for row in _extract_jsonld_ingredients(best.get("recipeIngredient")):
        name, amount = _split_amount_ingredient(row)
        if not name:
            continue
        key = name
        suffix = 2
        while key in ingredients:
            key = f"{name} ({suffix})"
            suffix += 1
        ingredients[key] = amount

    steps = _extract_jsonld_steps(best.get("recipeInstructions"))

    tags: list[str] = []
    categories = best.get("recipeCategory")
    if isinstance(categories, list):
        tags.extend(_clean_inline_text(item).lower() for item in categories if _clean_inline_text(item))
    else:
        cat_text = _clean_inline_text(categories or "")
        if cat_text:
            tags.append(cat_text.lower())
    keywords = _clean_inline_text(best.get("keywords") or "")
    if keywords:
        tags.extend(chunk.strip().lower() for chunk in keywords.split(",") if chunk.strip())
    dedup_tags: list[str] = []
    seen: set[str] = set()
    for tag in tags:
        if tag and tag not in seen:
            seen.add(tag)
            dedup_tags.append(tag)

    return {
        "Name": _clean_inline_text(best.get("name") or "") or "Receta",
        "Ingredients": ingredients,
        "Steps": steps,
        "Preparation time": _parse_iso_duration(best.get("prepTime") or ""),
        "Total time": _parse_iso_duration(best.get("totalTime") or ""),
        "Nºpersonas": _parse_persons(best.get("recipeYield") or ""),
        "card image": DEFAULT_CARD_COLOR,
        "Tags": dedup_tags,
        "Tools": [],
    }
