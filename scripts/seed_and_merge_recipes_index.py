#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import unquote


RECIPE_INDEX_FIELDNAMES = [
    "slug",
    "recipe_name",
    "tags",
    "tags_tools",
    "Verified",
    "image_location_in_bucket",
]


def _find_project_root(start: Path | None = None) -> Path:
    candidate = (start or Path.cwd()).resolve()
    for root in [candidate, *candidate.parents]:
        if (root / "src").exists() and (root / "data").exists():
            return root
    raise RuntimeError("Could not find project root (expected `src/` and `data/` folders).")


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


def _slugify(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower())
    return normalized.strip("-") or "receta"


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "si", "sí"}


def _normalize_list_values(value: Any) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        raw_values = [str(item or "").strip() for item in value]
    elif isinstance(value, str):
        raw_values = [chunk.strip() for chunk in value.split(",")]
    else:
        raw_values = []

    out: list[str] = []
    seen: set[str] = set()
    for raw in raw_values:
        lowered = raw.lower()
        if not lowered or lowered in seen:
            continue
        seen.add(lowered)
        out.append(lowered)
    return out


def _normalize_image_location_in_bucket(route: Any, slug: str, images_prefix: str) -> str:
    raw = str(route or "").strip()
    if not raw:
        return ""

    if raw.startswith("/media/"):
        return unquote(raw[len("/media/") :].lstrip("/"))
    if raw.startswith("media/"):
        return unquote(raw[len("media/") :].lstrip("/"))

    lowered = raw.lower()
    if lowered.startswith("http://") or lowered.startswith("https://") or lowered.startswith("data:"):
        return ""

    normalized_raw = raw.lstrip("/")
    if normalized_raw.startswith(f"{images_prefix}/"):
        return normalized_raw

    # Local generated exports usually store /images/recipes/<filename>.
    filename = Path(normalized_raw).name
    if filename:
        return f"{images_prefix}/{_slugify(slug)}/{filename}"
    return ""


def _record_from_payload(payload: dict[str, Any], *, slug_hint: str, images_prefix: str) -> dict[str, str]:
    slug = _slugify(str(payload.get("slug") or slug_hint))
    recipe_name = str(payload.get("Name") or payload.get("name") or slug.replace("-", " ").title()).strip() or "Receta"
    tags = _normalize_list_values(payload.get("Tags") if "Tags" in payload else payload.get("tags"))
    verified_raw = payload.get("Verified") if "Verified" in payload else payload.get("verified")
    image_route = payload.get("card image file") if "card image file" in payload else payload.get("card_image_file")
    image_location = _normalize_image_location_in_bucket(image_route, slug, images_prefix)
    return {
        "slug": slug,
        "recipe_name": recipe_name,
        "tags": ", ".join(tags),
        "tags_tools": "",
        "Verified": "true" if _as_bool(verified_raw) else "false",
        "image_location_in_bucket": image_location,
    }


def _write_csv_records(records_by_slug: dict[str, dict[str, str]], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    ordered_rows = sorted(records_by_slug.values(), key=lambda row: (str(row.get("recipe_name") or "").lower(), row["slug"]))
    with output_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=RECIPE_INDEX_FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        for row in ordered_rows:
            writer.writerow({field: str(row.get(field) or "") for field in RECIPE_INDEX_FIELDNAMES})


def _read_local_generated_records(local_json_dir: Path, images_prefix: str) -> dict[str, dict[str, str]]:
    records: dict[str, dict[str, str]] = {}
    files = sorted(
        p
        for p in local_json_dir.glob("*.json")
        if p.name not in {"_run_report.json", "_upload_report_to_webapp.json"}
    )
    for index, path in enumerate(files, 1):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            continue
        slug = _slugify(path.stem)
        records[slug] = _record_from_payload(payload, slug_hint=slug, images_prefix=images_prefix)
        if index % 1000 == 0:
            print(f"[local] parsed {index}/{len(files)} generated JSONs")
    print(f"[local] built {len(records)} records from {local_json_dir}")
    return records


def _upload_index_csv(index_blob: str, csv_path: Path) -> None:
    from src.gcs_storage import upload_bytes

    payload = csv_path.read_bytes()
    upload_bytes(payload, index_blob, content_type="text/csv; charset=utf-8", cache_seconds=0)


def _merge_bucket_only_recipes(
    *,
    records_by_slug: dict[str, dict[str, str]],
    recipes_prefix: str,
    index_blob: str,
    images_prefix: str,
) -> int:
    from src.gcs_storage import get_bucket, storage_client

    client = storage_client()
    bucket = get_bucket()
    prefix = f"{recipes_prefix}/"

    added = 0
    scanned_json_blobs = 0
    started = time.time()
    for blob in client.list_blobs(bucket, prefix=prefix):
        blob_name = str(getattr(blob, "name", "") or "").strip()
        if not blob_name.endswith(".json"):
            continue
        if blob_name == index_blob:
            continue

        scanned_json_blobs += 1
        slug = _slugify(Path(blob_name).stem)
        if slug in records_by_slug:
            continue

        try:
            raw = blob.download_as_bytes(client=client)
            payload = json.loads(raw.decode("utf-8"))
            if not isinstance(payload, dict):
                continue
            records_by_slug[slug] = _record_from_payload(payload, slug_hint=slug, images_prefix=images_prefix)
            added += 1
            if added <= 10 or added % 100 == 0:
                print(f"[bucket] added missing slug={slug} (missing_count={added})")
        except Exception as exc:  # noqa: BLE001
            print(f"[bucket] WARNING skipping {blob_name}: {exc.__class__.__name__}: {exc}")

        if scanned_json_blobs % 1000 == 0:
            elapsed = time.time() - started
            print(
                f"[bucket] scanned {scanned_json_blobs} recipe JSON blobs "
                f"(added_missing={added}) in {elapsed:.1f}s"
            )

    print(f"[bucket] merge complete: scanned={scanned_json_blobs}, added_missing={added}")
    return added


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Seed recipes_index.csv from data/recipes_generated, upload it to the bucket, "
            "then merge in bucket recipes missing from the local generated set."
        )
    )
    parser.add_argument(
        "--local-json-dir",
        default="data/recipes_generated",
        help="Local generated recipe JSON directory (default: data/recipes_generated)",
    )
    parser.add_argument(
        "--output-csv",
        default="data/recipes_generated/recipes_index.csv",
        help="Local CSV path to write (default: data/recipes_generated/recipes_index.csv)",
    )
    parser.add_argument(
        "--recipes-prefix",
        default=None,
        help="Bucket recipe JSON prefix (default from RECETAS_RECIPES_PREFIX or 'recipes')",
    )
    parser.add_argument(
        "--images-prefix",
        default=None,
        help="Bucket image prefix (default from RECETAS_IMAGES_PREFIX or 'recipes/images')",
    )
    parser.add_argument(
        "--index-blob",
        default=None,
        help="Bucket CSV blob path (default: <recipes-prefix>/recipes_index.csv)",
    )
    parser.add_argument(
        "--bucket-name",
        default=None,
        help="Bucket name override (exported to RECETAS_BUCKET_NAME before GCS import)",
    )
    parser.add_argument(
        "--key-file",
        default=None,
        help="Service account key path override (exported to API_BUCKET_KEY_FILE before GCS import)",
    )
    parser.add_argument(
        "--skip-upload",
        action="store_true",
        help="Do not upload the seed/final CSV to the bucket (local CSV only).",
    )
    args = parser.parse_args()

    project_root = _find_project_root()
    os.chdir(project_root)
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    # Load env files so bucket/key defaults work when run from shell.
    _load_simple_env_file(project_root / "env.prod")
    _load_simple_env_file(project_root / "secrets" / "env.prod")

    if args.bucket_name:
        os.environ["RECETAS_BUCKET_NAME"] = args.bucket_name
    if args.key_file:
        os.environ["API_BUCKET_KEY_FILE"] = args.key_file

    for env_var in ("API_BUCKET_KEY_FILE", "GOOGLE_APPLICATION_CREDENTIALS"):
        _normalize_path_env(env_var, project_root)

    local_json_dir = (project_root / args.local_json_dir).resolve()
    output_csv = (project_root / args.output_csv).resolve()
    recipes_prefix = (args.recipes_prefix or os.getenv("RECETAS_RECIPES_PREFIX") or "recipes").strip("/ ")
    images_prefix = (args.images_prefix or os.getenv("RECETAS_IMAGES_PREFIX") or "recipes/images").strip("/ ")
    index_blob = (args.index_blob or f"{recipes_prefix}/recipes_index.csv").strip("/ ")

    print("PROJECT_ROOT =", project_root)
    print("local_json_dir =", local_json_dir)
    print("output_csv =", output_csv)
    print("recipes_prefix =", recipes_prefix)
    print("images_prefix =", images_prefix)
    print("index_blob =", index_blob)
    print("skip_upload =", bool(args.skip_upload))
    print("RECETAS_BUCKET_NAME =", os.getenv("RECETAS_BUCKET_NAME", ""))
    print("API_BUCKET_KEY_FILE =", os.getenv("API_BUCKET_KEY_FILE", ""))

    started_total = time.time()
    records_by_slug = _read_local_generated_records(local_json_dir, images_prefix)
    _write_csv_records(records_by_slug, output_csv)
    print(f"[seed] wrote local CSV with {len(records_by_slug)} rows -> {output_csv}")

    if not args.skip_upload:
        _upload_index_csv(index_blob, output_csv)
        print(f"[seed] uploaded seed CSV -> {index_blob}")

    added_missing = 0
    if not args.skip_upload:
        added_missing = _merge_bucket_only_recipes(
            records_by_slug=records_by_slug,
            recipes_prefix=recipes_prefix,
            index_blob=index_blob,
            images_prefix=images_prefix,
        )
        _write_csv_records(records_by_slug, output_csv)
        print(f"[final] wrote merged local CSV with {len(records_by_slug)} rows -> {output_csv}")
        _upload_index_csv(index_blob, output_csv)
        print(f"[final] uploaded merged CSV -> {index_blob}")

    elapsed_total = time.time() - started_total
    print(
        f"Done: local_seed_rows={len(records_by_slug) - added_missing} "
        f"bucket_missing_added={added_missing} final_rows={len(records_by_slug)} "
        f"elapsed={elapsed_total:.1f}s"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
