import json
import logging
import os
import re
import string
import tempfile
import threading
import time
import unicodedata
from pathlib import Path
from typing import Dict, Iterable, Tuple
from difflib import SequenceMatcher

try:
    import json5  # type: ignore
except ModuleNotFoundError:  # pragma: no cover - optional dependency
    json5 = None

try:
    from Levenshtein import distance as levenshtein_distance
except ModuleNotFoundError:  # pragma: no cover - optional dependency
    def levenshtein_distance(a: str, b: str) -> int:
        if a == b:
            return 0
        ratio = SequenceMatcher(None, a, b).ratio()
        return int(round((1.0 - ratio) * max(len(a), len(b))))

try:
    from google import genai  # type: ignore
    from google.genai import errors as genai_errors  # type: ignore
    try:
        from google.genai import types as genai_types  # type: ignore
    except Exception:  # pragma: no cover - optional dependency shape can vary
        genai_types = None
except ModuleNotFoundError:  # pragma: no cover - optional dependency
    genai = None
    genai_errors = None
    genai_types = None

try:
    from unidecode import unidecode
except ModuleNotFoundError:  # pragma: no cover - optional dependency
    def unidecode(value: str) -> str:
        normalized = unicodedata.normalize("NFKD", str(value or ""))
        return normalized.encode("ascii", "ignore").decode("ascii")

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:  # pragma: no cover - optional dependency
    def load_dotenv(*args, **kwargs):
        return False
load_dotenv(f"secrets/env.prod")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
if genai is not None:
    _GENAI_CLIENT = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else genai.Client()
else:
    _GENAI_CLIENT = None

def _read_json5(path: Path) -> dict:
    if not path.exists():
        return {}
    raw = path.read_text(encoding="utf-8").replace("\\\r\n", "\\n").replace("\\\n", "\\n")
    if json5 is not None:
        return json5.loads(raw)
    return json.loads(raw)




logger = logging.getLogger(__name__)

Meta = Tuple[int, float]  # (size, mtime)


def _snapshot(dir_path: str) -> Dict[str, Meta]:
    paths: Dict[str, Meta] = {}
    if not os.path.exists(dir_path):
        return paths
    try:
        for root, _, files in os.walk(dir_path):
            for f in files:
                p = os.path.join(root, f)
                try:
                    st = os.stat(p)
                    paths[p] = (int(st.st_size), float(st.st_mtime))
                except Exception:
                    # best effort; skip if stat fails
                    continue
    except Exception:
        logger.exception("tmp_monitor: failed to walk %s", dir_path)
    return paths


_monitor_thread = None
_stop_flag = threading.Event()


def _warn(msg: str) -> None:
    # Always log in yellow for visibility
    logger.warning("\033[33m%s\033[0m", msg)


def enable_tmp_monitor_from_env() -> None:
    """Enable tmp monitoring with defaults; no env required.

    Watches the system temp gradio dir and GRADIO_TEMP_DIR (if set).
    Always reports existing files at startup; polls every 1.0s.
    """
    try:
        watch_dirs = []
        # Use the system temp directory on all platforms
        watch_dirs.append(os.path.join(tempfile.gettempdir(), "gradio"))
        # If user set GRADIO_TEMP_DIR, watch it as well
        gt = os.environ.get("GRADIO_TEMP_DIR")
        if gt:
            watch_dirs.append(gt)

        start_tmp_monitor(
            dirs=watch_dirs,
            poll_interval=1.0,
            include_existing=True,
            strict=False,
        )
    except Exception:
        logger.exception("tmp monitor: failed to start")


def start_tmp_monitor(
    dirs: Iterable[str] | None = None,
    poll_interval: float = 1.0,
    include_existing: bool = False,
    strict: bool = False,
) -> None:
    """
    Start a lightweight background monitor that prints when new files appear
    under the specified directories (defaults to GRADIO temp and OS /tmp if present).
    """
    global _monitor_thread
    if _monitor_thread and _monitor_thread.is_alive():
        return

    watch_dirs = list(dirs or [])
    # De-dup and keep only existing or interesting directories
    seen = set()
    final_dirs = []
    for d in watch_dirs:
        if not d or d in seen:
            continue
        seen.add(d)
        final_dirs.append(d)

    # Prepare initial snapshots
    baselines: Dict[str, Dict[str, Meta]] = {d: _snapshot(d) for d in final_dirs}

    # Optionally report existing files immediately
    if include_existing:
        for d, files in baselines.items():
            for p, (size, _) in sorted(files.items()):
                _warn(f"WARNING [TMP-MONITOR][EXISTING] {p} (size={size})")
        if strict:
            _warn("WARNING [TMP-MONITOR][ALERT] Existing files detected in monitored dirs.")

    def _run():
        logger.info("tmp_monitor: watching %s (interval=%ss)", ", ".join(final_dirs), poll_interval)
        while not _stop_flag.is_set():
            for d in final_dirs:
                before = baselines.get(d, {})
                after = _snapshot(d)

                before_keys = set(before.keys())
                after_keys = set(after.keys())

                new_files = after_keys - before_keys
                if new_files:
                    for p in sorted(new_files):
                        size, _ = after.get(p, (-1, 0.0))
                        _warn(f"WARNING [TMP-MONITOR] New file: {p} (size={size})")
                    if strict:
                        _warn("WARNING [TMP-MONITOR][ALERT] New files detected.\n")

                # Detect modified files (size or mtime changed)
                common = before_keys & after_keys
                modified = []
                for p in common:
                    bs, bm = before[p]
                    asz, am = after[p]
                    if asz != bs or am > bm:
                        modified.append((p, asz))
                if modified:
                    for p, size in sorted(modified):
                        _warn(f"WARNING [TMP-MONITOR] Updated file: {p} (size={size})")
                    if strict:
                        _warn("WARNING [TMP-MONITOR][ALERT] File updates detected.\n")

                baselines[d] = after
            _stop_flag.wait(timeout=poll_interval)
        logger.info("tmp_monitor: stopped")

    _stop_flag.clear()
    _monitor_thread = threading.Thread(target=_run, name="tmp-monitor", daemon=True)
    _monitor_thread.start()


def stop_tmp_monitor() -> None:
    global _monitor_thread
    if _monitor_thread and _monitor_thread.is_alive():
        _stop_flag.set()
        # Don't join indefinitely; it's a daemon thread anyway
        _monitor_thread.join(timeout=2.0)
        _monitor_thread = None


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("Invalid %s=%r; using default %s", name, raw, default)
        return default


DEFAULT_LLM_CALL_TIMEOUT_S = _env_float("LLM_CALL_TIMEOUT_SECONDS", 40.0)

_DESCRIPTIONS_DIR = Path(__file__).resolve().parent / "descriptions"


PROCESS_DESCRIPTIONS = _read_json5(_DESCRIPTIONS_DIR / "processes.json5")
PRODUS_DESCRIPTIONS = _read_json5(_DESCRIPTIONS_DIR / "produs.json5")


models=[
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.5-pro",
]

default_model = models[0]
fallback_models = models[1:]
model_retry_attempts = 2
default_file_ocr_model = "gemini-2.5-flash-lite"


class _LLMTimeoutError(TimeoutError):
    pass


def _call_with_timeout(fn, timeout_s: float, label: str):
    if timeout_s is None or timeout_s <= 0:
        return fn()

    result = {}
    error = {}
    done = threading.Event()

    def _run():
        try:
            result["value"] = fn()
        except Exception as exc:
            error["error"] = exc
        finally:
            done.set()

    thread = threading.Thread(target=_run, name="llm-call", daemon=True)
    thread.start()
    started = time.monotonic()
    if not done.wait(timeout=timeout_s):
        elapsed = time.monotonic() - started
        raise _LLMTimeoutError(f"{label} timed out after {elapsed:.1f}s")
    if "error" in error:
        raise error["error"]
    return result.get("value")


def _generate_content_with_fallback(model, contents, config, timeout_s: float | None = None):
    if _GENAI_CLIENT is None:
        raise RuntimeError(
            "google-genai is not installed. Install it (for example: `pip install google-genai`) "
            "to use Gemini-backed helpers in src/LLMs_funcs.py."
        )

    def _get_block_reason(response):
        prompt_feedback = getattr(response, "prompt_feedback", None)
        return None if not prompt_feedback else  getattr(prompt_feedback, "block_reason", None)

    def _is_retryable_server_error(exc: Exception) -> bool:
        if isinstance(exc, TimeoutError):
            return True
        if genai_errors and isinstance(exc, genai_errors.ServerError):
            status_code = getattr(exc, "status_code", None)
            if status_code == 503:
                return True
        message = str(exc).lower()
        return "overloaded" in message or "unavailable" in message

    if timeout_s is None:
        timeout_s = DEFAULT_LLM_CALL_TIMEOUT_S

    models_to_try = [model] + [m for m in fallback_models if m != model]
    last_response = None
    last_error = None
    for idx, current_model in enumerate(models_to_try):
        should_try_next_model = False
        for attempt in range(model_retry_attempts + 1):
            try:
                response = _call_with_timeout(
                    lambda: _GENAI_CLIENT.models.generate_content(
                        model=current_model,
                        contents=contents,
                        config=config,
                    ),
                    timeout_s=timeout_s,
                    label=f"LLM call (model={current_model})",
                )
            except Exception as exc:
                if _is_retryable_server_error(exc):
                    if attempt < model_retry_attempts:
                        if isinstance(exc, TimeoutError):
                            logger.warning(
                                "Model %s timed out after %.1fs; retrying (%s/%s)",
                                current_model,
                                timeout_s,
                                attempt + 1,
                                model_retry_attempts,
                            )
                        else:
                            logger.warning(
                                "Model %s failed with %s; retrying (%s/%s)",
                                current_model,
                                exc.__class__.__name__,
                                attempt + 1,
                                model_retry_attempts,
                            )
                        continue
                    last_error = exc
                    if idx < len(models_to_try) - 1:
                        logger.warning(
                            "Model %s failed after %s retries; trying fallback model %s",
                            current_model,
                            model_retry_attempts,
                            models_to_try[idx + 1],
                        )
                        should_try_next_model = True
                        break
                raise

            last_response = response
            block_reason = _get_block_reason(response)
            response_text = getattr(response, "text", None)
            if not response_text:
                prompt_feedback = getattr(response, "prompt_feedback", None)
                safety_ratings = getattr(prompt_feedback, "safety_ratings", None) if prompt_feedback else None
                candidates = getattr(response, "candidates", None)
                candidate_count = len(candidates) if candidates is not None else None
                logger.warning(
                    "LLM returned empty text (model=%s, block_reason=%s, candidates=%s, safety_ratings=%r).",
                    current_model,
                    block_reason,
                    candidate_count,
                    safety_ratings,
                )
            if block_reason:
                if idx < len(models_to_try) - 1:
                    logger.warning("Model %s blocked with %s; trying fallback model %s",current_model,block_reason,models_to_try[idx + 1])
                    should_try_next_model = True
                    break
            return response.text
        if should_try_next_model:
            continue
    if last_response is not None:
        return last_response.text
    if last_error is not None:
        raise last_error
    return last_response.text

def to_english_alphabet(text):
    #check text is a str
    if not isinstance(text, str):
        return text
    
    result = []
    for char in text:
        result.append(unidecode(char))

    unidecoded=''.join(result)

    # regex pattern to only retrieve letters and spaces
    pattern = r'[^A-Za-z/\s]'
    return re.sub(pattern, ' ', unidecoded)


def ensure_output_in_keys(
    output: str,
    expected_keys: list[str],
    possible_variations: dict[str, list[str]] | None = None,
    default: str | None = None,
) -> str:
    """
    Fixes LLM output by mapping it to one of expected_keys using:
    - exact match
    - case/diacritics-insensitive match
    - substring match on normalized text
    - Levenshtein distance (<= 1)
    - optional declared variations per expected key
      (e.g., {"INFORMATII PRODUS": ["INFO PRODUS", "INFORMATII"]} lets those
      variants map back to "INFORMATII PRODUS")
    """
    def _normalize_label(text: str) -> str:
        if not isinstance(text, str):
            return ""
        normalized = to_english_alphabet(text)
        return re.sub(r"\s+", " ", normalized).strip().lower()
    
    if possible_variations is None:
        possible_variations = {}

    if output in expected_keys:
        return output

    output_norm = _normalize_label(output)
    candidates: list[tuple[str, str]] = []
    for key in expected_keys:
        candidates.append((_normalize_label(key), key))
        for variation in possible_variations.get(key, []):
            candidates.append((_normalize_label(variation), key))

    # Direct normalized match
    for candidate_norm, key in candidates:
        if output_norm == candidate_norm:
            return key

    # Levenshtein match against candidates
    for candidate_norm, key in candidates:
        if levenshtein_distance(output_norm, candidate_norm) <= 1:
            return key

    if default is not None:
        logger.warning("LLM output %r did not match expected keys; defaulting to %r", output, default)
        return default
    
    return "Failed to match"
    print(f"LLM output did not match expected keys: {output!r}, \n expected: {expected_keys!r}")
    #raise ValueError(f"LLM output did not match expected keys: {output!r}, \n expected: {expected_keys!r}")


def turn_null_to_empty_string(input_dict):
    """Converts all null values in a dictionary to empty strings."""
    return {k: (v if str(v).lower()!="null" else "") for k, v in input_dict.items()}
    

def _extract_json_text(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text, flags=re.IGNORECASE).strip()
        if text.endswith("```"):
            text = text[:-3]
        return text.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start : end + 1]
    return text


def Turn_into_a_correct_json(
    doc: str,
    keys: list[str],
    *,
    list_keys: list[str] | None = None,
    model=default_model,
    force_english: bool = False,
) -> dict:
    key_list = "\n".join(f"- {k}" for k in keys)
    list_keys = list_keys or []
    list_keys_line = ""
    if list_keys:
        list_keys_line = f"Keys that must be JSON arrays: {', '.join(list_keys)}.\n"
    english_line = "All free-text values must be in English.\n" if force_english else ""
    info_extraction_prompt = f"""
        You are given text that is supposed to be a JSON object but may be invalid.
        This object must contain only the following keys:
        {key_list}

        {list_keys_line}{english_line}
        Extract the information from the input text and return a valid JSON object.
        If a key is missing, return an empty string (or [] for list keys).
    """

    response = _generate_content_with_fallback(
        model=model,
        contents=doc,
        config={"system_instruction": info_extraction_prompt},
    )
    raw_output = response.strip()
    json_text = _extract_json_text(raw_output)
    try:
        return json.loads(json_text)
    except Exception:
        if json5 is not None:
            try:
                return json5.loads(json_text)
            except Exception:
                pass
        return {"error": "Failed to parse cleaned response", "raw": json_text}


RECIPE_WEBAPP_KEYS = [
    "Name",
    "Ingredients",
    "Steps",
    "Preparation time",
    "Total time",
    "Nºpersonas",
    "Tags",
    "Tools",
]


def _normalize_recipe_list(value: object) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        raw_items = value
    elif isinstance(value, str):
        raw_items = [chunk.strip() for chunk in value.split(",")]
    else:
        raw_items = []

    cleaned: list[str] = []
    seen: set[str] = set()
    for raw_item in raw_items:
        text = str(raw_item or "").strip()
        normalized = text.lower()
        if not text or normalized in seen:
            continue
        seen.add(normalized)
        cleaned.append(text)
    return cleaned


def _normalize_recipe_ingredients(value: object) -> dict[str, str]:
    if isinstance(value, dict):
        items = value.items()
    elif isinstance(value, (list, tuple)):
        items = []
        for row in value:
            if isinstance(row, dict):
                name = str(row.get("name") or row.get("ingredient") or "").strip()
                amount = str(row.get("amount") or row.get("qty") or "").strip()
                items.append((name, amount))
            elif isinstance(row, (list, tuple)) and len(row) >= 2:
                items.append((str(row[0] or "").strip(), str(row[1] or "").strip()))
    else:
        items = []

    normalized: dict[str, str] = {}
    for raw_name, raw_amount in items:
        name = str(raw_name or "").strip()
        amount = str(raw_amount or "").strip()
        if not name and not amount:
            continue
        if not name:
            name = f"ingredient-{len(normalized) + 1}"
        normalized[name] = amount
    return normalized


def normalize_recipe_webapp_payload(
    payload: dict,
    *,
    default_card_color: str = "rgb(118, 161, 146)",
) -> dict:
    if not isinstance(payload, dict):
        payload = {}
    _ = default_card_color  # legacy arg kept for call-site compatibility; color fallback is code-side only

    normalized = {
        "Name": str(payload.get("Name") or payload.get("name") or "").strip() or "Receta",
        "Ingredients": _normalize_recipe_ingredients(
            payload.get("Ingredients") if "Ingredients" in payload else payload.get("ingredients")
        ),
        "Steps": _normalize_recipe_list(payload.get("Steps") if "Steps" in payload else payload.get("steps")),
        "Preparation time": str(
            payload.get("Preparation time") or payload.get("preparation_time") or "No especificado"
        ).strip()
        or "No especificado",
        "Total time": str(payload.get("Total time") or payload.get("total_time") or "No especificado").strip()
        or "No especificado",
        "Nºpersonas": str(payload.get("Nºpersonas") or payload.get("n_personas") or payload.get("persons") or "").strip()
        or "No especificado",
        "Tags": _normalize_recipe_list(payload.get("Tags") if "Tags" in payload else payload.get("tags")),
        "Tools": _normalize_recipe_list(payload.get("Tools") if "Tools" in payload else payload.get("tools")),
    }

    card_image_file = str(payload.get("card image file") or payload.get("card_image_file") or "").strip()
    if card_image_file:
        normalized["card image file"] = card_image_file

    if "Verified" in payload or "verified" in payload:
        normalized["Verified"] = bool(payload.get("Verified") if "Verified" in payload else payload.get("verified"))

    return normalized


def recipe_text_to_webapp_json(
    doc: str,
    *,
    model=default_model,
    force_english: bool = False,
    default_card_color: str = "rgb(118, 161, 146)",
) -> dict:
    key_list = "\n".join(f"- {key}" for key in RECIPE_WEBAPP_KEYS)
    language_line = "Return all free-text values in English.\n" if force_english else "Return all free-text values in Spanish.\n"
    prompt = f"""
        Convert the input into a recipe JSON object for a web application.
        Return JSON only (no markdown, no comments), using exactly these keys:
        {key_list}

        Rules:
        - "Ingredients" must be a JSON object mapping ingredient name -> amount.
        - "Steps", "Tags", and "Tools" must be JSON arrays of strings.
        - Preserve the source step structure exactly: keep the same step order and the same number of steps found in the recipe.
        - For "Steps", transcribe the original step text as literally as possible from the source. Do not summarize, merge, split, rewrite, or paraphrase steps.
        - Keep quantities, times, temperatures, and wording in each step exactly as shown whenever readable.
        - If data is missing, use "No especificado" for time/servings, [] for lists, and {{}} for ingredients.
        - Keep names and ingredient wording concise.
        - Do not invent unsafe cooking instructions.
        {language_line}
    """
    response = _generate_content_with_fallback(
        model=model,
        contents=doc,
        config={
            "system_instruction": prompt,
            "response_mime_type": "application/json",
        },
    )
    raw_output = str(response or "").strip()
    json_text = _extract_json_text(raw_output)
    parsed: dict
    try:
        parsed = json.loads(json_text)
    except Exception:
        if json5 is not None:
            try:
                parsed = json5.loads(json_text)
            except Exception:
                parsed = {}
        else:
            parsed = {}
    return normalize_recipe_webapp_payload(parsed, default_card_color=default_card_color)


def recipe_file_to_webapp_json(
    file_bytes: bytes,
    mime_type: str,
    *,
    context_text: str = "",
    model=default_file_ocr_model,
    force_english: bool = False,
    default_card_color: str = "rgb(118, 161, 146)",
) -> dict:
    if _GENAI_CLIENT is None:
        raise RuntimeError(
            "google-genai is not installed. Install it (for example: `pip install google-genai`) "
            "to use Gemini-backed helpers in src/LLMs_funcs.py."
        )
    if not file_bytes:
        raise ValueError("file_bytes is empty")

    key_list = "\n".join(f"- {key}" for key in RECIPE_WEBAPP_KEYS)
    language_line = (
        "Return all free-text values in English.\n"
        if force_english
        else "Return all free-text values in Spanish.\n"
    )
    system_prompt = f"""
        Convert the attached file into a recipe JSON object for a web application.
        The file may be an image or a document that requires OCR/reading.
        Return JSON only (no markdown, no comments), using exactly these keys:
        {key_list}

        Rules:
        - "Ingredients" must be a JSON object mapping ingredient name -> amount.
        - "Steps", "Tags", and "Tools" must be JSON arrays of strings.
        - Preserve the source step structure exactly: keep the same step order and the same number of steps found in the recipe.
        - For "Steps", transcribe the original step text as literally as possible from the file/OCR. Do not summarize, merge, split, rewrite, or paraphrase steps.
        - Keep quantities, times, temperatures, and wording in each step exactly as shown whenever readable.
        - If data is missing, use "No especificado" for time/servings, [] for lists, and {{}} for ingredients.
        - Keep names and ingredient wording concise.
        - Do not invent unsafe cooking instructions.
        {language_line}
    """

    user_text = str(context_text or "").strip() or "Extrae la receta del archivo adjunto."
    contents = None

    if genai_types is not None and hasattr(genai_types, "Part"):
        try:
            file_part = genai_types.Part.from_bytes(data=file_bytes, mime_type=str(mime_type or "").strip() or "application/octet-stream")
            contents = [user_text, file_part]
        except Exception:
            contents = None

    if contents is None:
        raise RuntimeError(
            "This google-genai installation does not expose `types.Part.from_bytes`; "
            "update `google-genai` to use Gemini OCR/file import."
        )

    response = _generate_content_with_fallback(
        model=model,
        contents=contents,
        config={
            "system_instruction": system_prompt,
            "response_mime_type": "application/json",
        },
    )
    raw_output = str(response or "").strip()
    json_text = _extract_json_text(raw_output)
    parsed: dict
    try:
        parsed = json.loads(json_text)
    except Exception:
        if json5 is not None:
            try:
                parsed = json5.loads(json_text)
            except Exception:
                parsed = {}
        else:
            parsed = {}
    return normalize_recipe_webapp_payload(parsed, default_card_color=default_card_color)
