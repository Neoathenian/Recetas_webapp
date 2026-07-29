#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any


PROGRESS_EVERY = 25


def _find_project_root(start: Path | None = None) -> Path:
    candidate = (start or Path.cwd()).resolve()
    for root in [candidate, *candidate.parents]:
        if (root / "src").exists():
            return root
    raise RuntimeError("Could not find project root (expected a `src/` folder).")


def _load_simple_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


def _normalize_path_env(var_name: str, project_root: Path) -> None:
    value = (os.getenv(var_name) or "").strip()
    if not value:
        return
    path = Path(value)
    if not path.is_absolute():
        path = (project_root / path).resolve()
    os.environ[var_name] = str(path)


def _raw_category_values(value: Any) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        raw_values = [str(item or "").strip() for item in value]
    elif isinstance(value, str):
        raw_values = [chunk.strip() for chunk in value.split(",")]
    else:
        raw_values = []
    return [item for item in raw_values if item]


def _prepare_next_payload(payload: dict[str, Any]) -> tuple[dict[str, Any], list[str], list[str]]:
    from src.recipe_categories import filter_baseline_recipe_categories, normalize_recipe_category_key

    raw_tags = payload.get("Tags") if "Tags" in payload else payload.get("tags")
    before = _raw_category_values(raw_tags)
    after = filter_baseline_recipe_categories(raw_tags)
    allowed_after_keys = {normalize_recipe_category_key(value) for value in after}
    removed = [
        value
        for value in before
        if normalize_recipe_category_key(value) not in allowed_after_keys
    ]

    next_payload = dict(payload)
    next_payload["Tags"] = after
    next_payload.pop("tags", None)
    return next_payload, before, removed


def _payload_changed(payload: dict[str, Any], next_payload: dict[str, Any]) -> bool:
    return payload != next_payload


def _write_json_blob(blob: Any, payload: dict[str, Any], *, client: Any, timeout: float) -> None:
    body = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    upload_kwargs: dict[str, Any] = {
        "content_type": "application/json; charset=utf-8",
        "timeout": timeout,
    }
    generation = getattr(blob, "generation", None)
    if generation is not None:
        upload_kwargs["if_generation_match"] = generation
    blob.upload_from_string(body.encode("utf-8"), client=client, **upload_kwargs)


def _upload_index_csv(bucket: Any, index_blob: str, rows: list[dict[str, str]], *, timeout: float) -> None:
    from src.pages.recetas_list.core_the_list import RECIPE_INDEX_FIELDNAMES

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=RECIPE_INDEX_FIELDNAMES, extrasaction="ignore")
    writer.writeheader()
    for row in sorted(rows, key=lambda item: str(item.get("recipe_name") or "").lower()):
        writer.writerow({field: str(row.get(field) or "") for field in RECIPE_INDEX_FIELDNAMES})

    bucket.blob(index_blob).upload_from_string(
        (output.getvalue() or "").encode("utf-8"),
        content_type="text/csv; charset=utf-8",
        timeout=timeout,
    )


def _rebuild_recipe_index_with_timeouts(
    *,
    client: Any,
    bucket: Any,
    recipes_prefix: str,
    index_blob: str,
    timeout: float,
    progress_every: int,
) -> int:
    from src.pages.recetas_list.core_the_list import (
        _recipe_index_record_from_recipe,
        _recipe_slug_from_blob_name,
        _recipe_from_payload,
    )

    prefix = f"{recipes_prefix}/"
    rows: list[dict[str, str]] = []
    scanned = 0
    errors = 0
    started = time.time()

    print("[index] listing and reading recipe JSON blobs...", flush=True)
    for blob in client.list_blobs(bucket, prefix=prefix, timeout=timeout):
        blob_name = str(getattr(blob, "name", "") or "").strip()
        if not blob_name.endswith(".json") or blob_name == index_blob:
            continue

        scanned += 1
        try:
            payload = json.loads(blob.download_as_bytes(client=client, timeout=timeout).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("payload is not a JSON object")
            recipe = _recipe_from_payload(payload, slug_hint=_recipe_slug_from_blob_name(blob_name))
            rows.append(_recipe_index_record_from_recipe(recipe))
        except Exception as exc:  # noqa: BLE001
            errors += 1
            print(f"[index:error] {blob_name}: {exc.__class__.__name__}: {exc}", flush=True)

        if scanned % progress_every == 0:
            print(
                f"[index:progress] scanned={scanned} rows={len(rows)} errors={errors} last_blob={blob_name}",
                flush=True,
            )

    if errors:
        raise RuntimeError(f"Index rebuild had {errors} recipe read errors; not uploading CSV.")

    _upload_index_csv(bucket, index_blob, rows, timeout=timeout)
    print(f"[index] uploaded {len(rows)} rows -> {index_blob} elapsed={time.time() - started:.1f}s", flush=True)
    return len(rows)


def _validate_index_categories(bucket: Any, index_blob: str, *, timeout: float) -> int:
    from src.recipe_categories import normalize_recipe_category_key, BASELINE_RECIPE_CATEGORY_KEYS

    raw = bucket.blob(index_blob).download_as_bytes(timeout=timeout)
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
    invalid_counts: Counter[str] = Counter()
    rows = 0
    tagged_rows = 0
    for row in reader:
        rows += 1
        tags_text = str((row or {}).get("tags") or "").strip()
        if not tags_text:
            continue
        tagged_rows += 1
        for raw_tag in _raw_category_values(tags_text):
            if normalize_recipe_category_key(raw_tag) not in BASELINE_RECIPE_CATEGORY_KEYS:
                invalid_counts[raw_tag.lower()] += 1

    print("=== Index Category Validation ===")
    print(f"index_blob: {index_blob}")
    print(f"rows: {rows}")
    print(f"rows_with_tags: {tagged_rows}")
    print(f"invalid_category_count: {sum(invalid_counts.values())}")
    if invalid_counts:
        print("Invalid categories:")
        for tag, count in invalid_counts.most_common():
            print(f"- {tag}: {count}")
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Remove recipe categories outside the allowed recipe category list from GCS JSON payloads."
    )
    parser.add_argument("--recipes-prefix", default=None, help="Recipe JSON prefix (default: RECETAS_RECIPES_PREFIX or recipes)")
    parser.add_argument("--index-blob", default=None, help="Recipe index CSV blob (default: <recipes-prefix>/recipes_index.csv)")
    parser.add_argument("--bucket-name", default=None, help="Bucket override (sets RECETAS_BUCKET_NAME)")
    parser.add_argument("--key-file", default=None, help="Service account key override (sets API_BUCKET_KEY_FILE)")
    parser.add_argument("--dry-run", action="store_true", help="Report changes without writing JSONs or rebuilding the index")
    parser.add_argument("--limit", type=int, default=0, help="Stop after scanning this many recipe JSONs")
    parser.add_argument("--sample-size", type=int, default=25, help="Number of changed recipes to print")
    parser.add_argument("--skip-index-rebuild", action="store_true", help="Do not rebuild recipes_index.csv after writing")
    parser.add_argument("--timeout", type=float, default=60.0, help="Per-request GCS timeout in seconds")
    parser.add_argument("--progress-every", type=int, default=PROGRESS_EVERY, help="Print progress every N recipe JSONs")
    parser.add_argument("--validate-index-only", action="store_true", help="Only validate tags in the recipe index CSV against the cleanup baseline")
    args = parser.parse_args()

    project_root = _find_project_root()
    os.chdir(project_root)
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    _load_simple_env_file(project_root / "env.prod")
    _load_simple_env_file(project_root / "secrets" / "env.prod")

    if args.bucket_name:
        os.environ["RECETAS_BUCKET_NAME"] = args.bucket_name
    if args.key_file:
        os.environ["API_BUCKET_KEY_FILE"] = args.key_file
    if args.recipes_prefix:
        os.environ["RECETAS_RECIPES_PREFIX"] = args.recipes_prefix
    if args.index_blob:
        os.environ["RECETAS_RECIPE_INDEX_BLOB"] = args.index_blob
    if not (os.getenv("API_BUCKET_KEY_FILE") or "").strip() and (os.getenv("SERVICE_ACCOUNT_KEY_FILE") or "").strip():
        os.environ["API_BUCKET_KEY_FILE"] = os.getenv("SERVICE_ACCOUNT_KEY_FILE", "")

    for env_var in ("API_BUCKET_KEY_FILE", "GOOGLE_APPLICATION_CREDENTIALS"):
        _normalize_path_env(env_var, project_root)

    from src.gcs_storage import get_bucket, storage_client
    from src.recipe_categories import BASELINE_RECIPE_CATEGORIES

    recipes_prefix = (args.recipes_prefix or os.getenv("RECETAS_RECIPES_PREFIX") or "recipes").strip("/ ")
    index_blob = (args.index_blob or os.getenv("RECETAS_RECIPE_INDEX_BLOB") or f"{recipes_prefix}/recipes_index.csv").strip("/ ")
    prefix = f"{recipes_prefix}/"

    client = storage_client()
    bucket = get_bucket()

    print("PROJECT_ROOT =", project_root)
    print("bucket =", bucket.name)
    print("recipes_prefix =", recipes_prefix)
    print("index_blob =", index_blob)
    print("dry_run =", bool(args.dry_run))
    print("timeout =", float(args.timeout))
    print("baseline_categories =", ", ".join(BASELINE_RECIPE_CATEGORIES))

    if args.validate_index_only:
        return _validate_index_categories(bucket, index_blob, timeout=float(args.timeout))

    started = time.time()
    scanned = 0
    changed = 0
    unchanged = 0
    errors = 0
    removed_counts: Counter[str] = Counter()
    sample_rows: list[tuple[str, list[str], list[str]]] = []

    progress_every = max(1, int(args.progress_every or PROGRESS_EVERY))
    for blob in client.list_blobs(bucket, prefix=prefix, timeout=float(args.timeout)):
        blob_name = str(getattr(blob, "name", "") or "").strip()
        if not blob_name.endswith(".json") or blob_name == index_blob:
            continue
        scanned += 1
        if args.limit and scanned > args.limit:
            break

        try:
            payload = json.loads(blob.download_as_bytes(client=client, timeout=float(args.timeout)).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("payload is not a JSON object")
            next_payload, before, removed = _prepare_next_payload(payload)
            if _payload_changed(payload, next_payload):
                changed += 1
                for tag in removed:
                    removed_counts[tag.lower()] += 1
                if len(sample_rows) < max(0, args.sample_size):
                    sample_rows.append((blob_name, before, next_payload["Tags"]))
                if not args.dry_run:
                    _write_json_blob(blob, next_payload, client=client, timeout=float(args.timeout))
            else:
                unchanged += 1
        except Exception as exc:  # noqa: BLE001
            errors += 1
            print(f"[error] {blob_name}: {exc.__class__.__name__}: {exc}", flush=True)

        if scanned % progress_every == 0:
            print(
                f"[progress] scanned={scanned} changed={changed} unchanged={unchanged} errors={errors} last_blob={blob_name}",
                flush=True,
            )

    print("")
    print("=== Category Prune Summary ===")
    print(f"scanned_json_recipes: {scanned}")
    print(f"changed_recipes: {changed}")
    print(f"unchanged_recipes: {unchanged}")
    print(f"errors: {errors}")
    print(f"elapsed_seconds: {time.time() - started:.1f}")

    if sample_rows:
        print("")
        print("Sample changes:")
        for blob_name, before, after in sample_rows:
            print(f"- {blob_name}: {before} -> {after}")

    if removed_counts:
        print("")
        print("Removed category counts:")
        for tag, count in removed_counts.most_common():
            print(f"- {tag}: {count}")

    if not args.dry_run and not args.skip_index_rebuild:
        print("")
        print("Rebuilding recipe index CSV...")
        rebuilt_rows = _rebuild_recipe_index_with_timeouts(
            client=client,
            bucket=bucket,
            recipes_prefix=recipes_prefix,
            index_blob=index_blob,
            timeout=float(args.timeout),
            progress_every=progress_every,
        )
        print(f"Rebuilt index rows: {rebuilt_rows}")

    if args.dry_run:
        print("")
        print("Dry run only. Re-run without --dry-run to write changes and rebuild the index.")

    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
