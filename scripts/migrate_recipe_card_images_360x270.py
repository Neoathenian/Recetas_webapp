#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import io
import sys
from dataclasses import dataclass
from typing import Iterable
from urllib.parse import unquote

from google.cloud import storage
from google.oauth2 import service_account
from PIL import Image, ImageOps


CARD_SIZE = (360, 270)
INDEX_BLOB_DEFAULT = "recipes/recipes_index.csv"
JPEG_QUALITY = 82
WEBP_QUALITY = 80
PROGRESS_EVERY = 25


CONTENT_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
}


@dataclass
class Stats:
    total_refs: int = 0
    unique_blobs: int = 0
    missing_blobs: int = 0
    non_image_or_unsupported: int = 0
    rewritten: int = 0
    unchanged: int = 0
    errors: int = 0
    before_total: int = 0
    after_total: int = 0


def _normalize_blob_path(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if raw.startswith("/media/"):
        return unquote(raw[len("/media/") :].lstrip("/"))
    if raw.startswith("media/"):
        return unquote(raw[len("media/") :].lstrip("/"))
    if raw.startswith("http://") or raw.startswith("https://") or raw.startswith("data:"):
        return ""
    return raw.lstrip("/")


def _iter_index_image_blobs(bucket: storage.Bucket, index_blob_name: str) -> tuple[list[str], int]:
    blob = bucket.blob(index_blob_name)
    raw = blob.download_as_bytes()
    text = raw.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))

    refs: list[str] = []
    total_refs = 0
    for row in reader:
        if not isinstance(row, dict):
            continue
        total_refs += 1
        candidate = _normalize_blob_path(row.get("image_location_in_bucket") or "")
        if candidate:
            refs.append(candidate)

    # preserve order while de-duping
    unique = list(dict.fromkeys(refs))
    return unique, total_refs


def _optimize_bytes_to_card(image_bytes: bytes, extension: str) -> tuple[bytes, str, str]:
    ext = str(extension or "").lower()
    if ext in {".svg", ".gif"}:
        return image_bytes, ext, "skip_non_raster"

    if ext not in {".png", ".jpg", ".jpeg", ".webp"}:
        return image_bytes, ext, "skip_unsupported"

    with Image.open(io.BytesIO(image_bytes)) as img:
        img = ImageOps.exif_transpose(img)
        resampling = getattr(Image, "Resampling", Image).LANCZOS
        fitted = ImageOps.fit(img, CARD_SIZE, method=resampling)
        out = io.BytesIO()

        if ext in {".jpg", ".jpeg"}:
            if fitted.mode not in ("RGB", "L"):
                fitted = fitted.convert("RGB")
            fitted.save(out, format="JPEG", optimize=True, quality=JPEG_QUALITY, progressive=True)
            return out.getvalue(), ext, "optimized"

        if ext == ".png":
            if fitted.mode not in ("RGB", "RGBA", "L", "LA", "P"):
                fitted = fitted.convert("RGBA")
            fitted.save(out, format="PNG", optimize=True)
            return out.getvalue(), ext, "optimized"

        if ext == ".webp":
            if fitted.mode not in ("RGB", "RGBA", "L", "LA"):
                fitted = fitted.convert("RGBA")
            fitted.save(out, format="WEBP", quality=WEBP_QUALITY, method=6)
            return out.getvalue(), ext, "optimized"

    return image_bytes, ext, "skip_unsupported"


def _ext_from_name_or_type(blob_name: str, content_type: str | None) -> str:
    lower_name = (blob_name or "").lower()
    for ext in (".jpeg", ".jpg", ".png", ".webp", ".gif", ".svg"):
        if lower_name.endswith(ext):
            return ext
    ct = (content_type or "").lower().split(";", 1)[0].strip()
    for ext, mapped in CONTENT_TYPES.items():
        if mapped == ct:
            return ext
    return ""


def _human_gb(nbytes: int) -> float:
    return nbytes / (1024 ** 3)


def _human_mb(nbytes: float) -> float:
    return nbytes / (1024 ** 2)


def _iter_blobs(client: storage.Client, bucket: storage.Bucket, names: Iterable[str], *, dry_run: bool) -> Stats:
    stats = Stats()
    unique_names = [name for name in names if str(name or "").strip()]
    stats.unique_blobs = len(unique_names)

    for idx, blob_name in enumerate(unique_names, 1):
        blob = bucket.blob(blob_name)
        try:
            blob.reload(client=client)
        except Exception:
            stats.missing_blobs += 1
            continue

        try:
            original_bytes = blob.download_as_bytes(client=client)
        except Exception:
            stats.errors += 1
            continue

        before_size = len(original_bytes)
        stats.before_total += before_size

        ext = _ext_from_name_or_type(blob_name, blob.content_type)
        if not ext:
            stats.non_image_or_unsupported += 1
            stats.after_total += before_size
            continue

        try:
            optimized_bytes, optimized_ext, status = _optimize_bytes_to_card(original_bytes, ext)
        except Exception:
            stats.errors += 1
            stats.after_total += before_size
            continue

        after_size = len(optimized_bytes)
        stats.after_total += after_size

        if status.startswith("skip_"):
            stats.non_image_or_unsupported += 1
        elif optimized_bytes == original_bytes:
            stats.unchanged += 1
        else:
            if not dry_run:
                content_type = CONTENT_TYPES.get(optimized_ext) or blob.content_type or "application/octet-stream"
                existing_cache_control = blob.cache_control
                if existing_cache_control is not None:
                    blob.cache_control = existing_cache_control
                blob.upload_from_string(optimized_bytes, content_type=content_type, if_generation_match=blob.generation)
            stats.rewritten += 1

        if idx % PROGRESS_EVERY == 0:
            current_saved = stats.before_total - stats.after_total
            print(
                f"[{idx}/{stats.unique_blobs}] rewritten={stats.rewritten} unchanged={stats.unchanged} "
                f"skipped={stats.non_image_or_unsupported} errors={stats.errors} "
                f"net_saved_mb={_human_mb(current_saved):.2f}",
                flush=True,
            )

    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate recipe card images to canonical 360x270 size.")
    parser.add_argument("--bucket", required=True, help="GCS bucket name")
    parser.add_argument("--key-file", required=True, help="Service account JSON key path")
    parser.add_argument("--index-blob", default=INDEX_BLOB_DEFAULT, help=f"Recipe index CSV blob (default: {INDEX_BLOB_DEFAULT})")
    parser.add_argument("--dry-run", action="store_true", help="Compute savings without writing changes")
    args = parser.parse_args()

    creds = service_account.Credentials.from_service_account_file(args.key_file)
    client = storage.Client(credentials=creds, project=creds.project_id)
    bucket = client.bucket(args.bucket)

    image_blobs, total_refs = _iter_index_image_blobs(bucket, args.index_blob)
    print(
        f"Loaded {len(image_blobs)} unique image blobs from {total_refs} recipe index rows "
        f"({args.index_blob}) in gs://{args.bucket}",
        flush=True,
    )

    stats = _iter_blobs(client, bucket, image_blobs, dry_run=args.dry_run)
    stats.total_refs = total_refs

    total_saved = stats.before_total - stats.after_total
    avg_per_unique = (total_saved / stats.unique_blobs) if stats.unique_blobs else 0.0
    avg_per_rewritten = (total_saved / stats.rewritten) if stats.rewritten else 0.0

    print("")
    print("=== Migration Summary ===")
    print(f"bucket: {args.bucket}")
    print(f"index_blob: {args.index_blob}")
    print(f"dry_run: {args.dry_run}")
    print(f"recipe_rows_in_index: {stats.total_refs}")
    print(f"unique_image_blobs: {stats.unique_blobs}")
    print(f"rewritten_blobs: {stats.rewritten}")
    print(f"unchanged_blobs: {stats.unchanged}")
    print(f"skipped_non_raster_or_unsupported: {stats.non_image_or_unsupported}")
    print(f"missing_blobs: {stats.missing_blobs}")
    print(f"errors: {stats.errors}")
    print(f"before_total_bytes: {stats.before_total}")
    print(f"after_total_bytes: {stats.after_total}")
    print(f"saved_total_bytes: {total_saved}")
    print(f"saved_total_gib: {_human_gb(total_saved):.6f}")
    print(f"saved_avg_mib_per_unique_image: {_human_mb(avg_per_unique):.6f}")
    print(f"saved_avg_mib_per_rewritten_image: {_human_mb(avg_per_rewritten):.6f}")

    return 0 if stats.errors == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
