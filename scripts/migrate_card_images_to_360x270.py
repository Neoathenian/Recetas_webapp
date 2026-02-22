#!/usr/bin/env python3
from __future__ import annotations

import argparse
import io
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable
from urllib.parse import unquote, urlsplit

import requests
from PIL import Image, ImageOps
from sqlalchemy import text

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:  # pragma: no cover
    def load_dotenv(*_args, **_kwargs):  # type: ignore[no-redef]
        return False

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.db import session_scope
from src.gcs_storage import bucket_name as default_bucket_name
from src.gcs_storage import media_path, storage_client

TARGET_WIDTH = 360
TARGET_HEIGHT = 270
TARGET_RATIO = TARGET_WIDTH / TARGET_HEIGHT
DEFAULT_MEDIA_PREFIX = "the-list/uploads"

try:
    LANCZOS = Image.Resampling.LANCZOS
except AttributeError:  # pragma: no cover
    LANCZOS = Image.LANCZOS


@dataclass
class Stats:
    scanned: int = 0
    migrated: int = 0
    updated_rows: int = 0
    skipped_placeholder: int = 0
    skipped_empty: int = 0
    skipped_already_target: int = 0
    skipped_unhandled_url: int = 0
    skipped_no_blob: int = 0
    external_downloads: int = 0
    reused_conversions: int = 0
    errors: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "scanned": self.scanned,
            "migrated": self.migrated,
            "updated_rows": self.updated_rows,
            "skipped_placeholder": self.skipped_placeholder,
            "skipped_empty": self.skipped_empty,
            "skipped_already_target": self.skipped_already_target,
            "skipped_unhandled_url": self.skipped_unhandled_url,
            "skipped_no_blob": self.skipped_no_blob,
            "external_downloads": self.external_downloads,
            "reused_conversions": self.reused_conversions,
            "errors": self.errors,
        }


def _slugify(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", (value or "").strip().lower())
    return normalized.strip("-") or "item"


def _is_placeholder_image(url_value: str) -> bool:
    text = str(url_value or "").strip().lower()
    return text.startswith("/images/") or text == ""


def _extract_blob_path(url_value: str) -> str | None:
    text = str(url_value or "").strip()
    if not text:
        return None

    if text.startswith("/media/"):
        parsed = urlsplit(text)
        return unquote(parsed.path[len("/media/") :]).lstrip("/")

    parsed = urlsplit(text)
    if parsed.scheme in {"http", "https"} and parsed.path.startswith("/media/"):
        return unquote(parsed.path[len("/media/") :]).lstrip("/")

    if not parsed.scheme and not text.startswith("/"):
        return text.lstrip("/")

    return None


def _resolve_bucket_name(raw_bucket: Any) -> str:
    return str(raw_bucket or "").strip() or default_bucket_name()


def _center_crop_box(width: int, height: int, target_ratio: float) -> tuple[int, int, int, int]:
    source_ratio = width / height
    if source_ratio > target_ratio:
        crop_height = height
        crop_width = int(round(height * target_ratio))
        left = max(0, (width - crop_width) // 2)
        top = 0
    else:
        crop_width = width
        crop_height = int(round(width / target_ratio))
        left = 0
        top = max(0, (height - crop_height) // 2)

    right = min(width, left + crop_width)
    bottom = min(height, top + crop_height)
    return left, top, right, bottom


def _transform_to_target_png(source_bytes: bytes) -> tuple[bytes, int, int]:
    with Image.open(io.BytesIO(source_bytes)) as image_raw:
        image = ImageOps.exif_transpose(image_raw)
        width, height = image.size
        if width <= 0 or height <= 0:
            raise ValueError("Invalid source dimensions.")

        if image.mode not in {"RGB", "RGBA"}:
            has_alpha = "A" in image.getbands()
            image = image.convert("RGBA" if has_alpha else "RGB")

        crop_box = _center_crop_box(width, height, TARGET_RATIO)
        cropped = image.crop(crop_box)
        resized = cropped.resize((TARGET_WIDTH, TARGET_HEIGHT), LANCZOS)

        output = io.BytesIO()
        resized.save(output, format="PNG", optimize=True)
        return output.getvalue(), width, height


def _target_blob_name(
    *,
    source_blob: str | None,
    fallback_prefix: str,
    fallback_key: str,
) -> str:
    if source_blob:
        source_path = PurePosixPath(source_blob)
        stem = source_path.stem
        if not stem.endswith("-360x270"):
            stem = f"{stem}-360x270"
        filename = f"{stem}.png"
        parent = "" if str(source_path.parent) == "." else str(source_path.parent)
        return f"{parent}/{filename}".lstrip("/") if parent else filename

    prefix = fallback_prefix.strip("/") or DEFAULT_MEDIA_PREFIX
    key = _slugify(fallback_key)
    return f"{prefix}/{key}-360x270.png"


def _download_bytes(
    *,
    url_value: str,
    bucket_value: str,
    allow_external_downloads: bool,
    client,
    bucket_cache: dict[str, Any],
) -> tuple[bytes | None, str | None, str | None, bool]:
    """
    Returns: (payload, content_type, source_blob_path, used_external_download)
    """
    blob_path = _extract_blob_path(url_value)
    if blob_path:
        bucket = bucket_cache.setdefault(bucket_value, client.bucket(bucket_value))
        blob = bucket.blob(blob_path)
        payload = blob.download_as_bytes(client=client)
        return payload, blob.content_type, blob_path, False

    parsed = urlsplit(str(url_value or "").strip())
    if parsed.scheme in {"http", "https"} and allow_external_downloads:
        response = requests.get(str(url_value), timeout=30)
        response.raise_for_status()
        return response.content, response.headers.get("content-type"), None, True

    return None, None, None, False


def _load_rows(session, *, include_people: bool, include_sources: bool) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    people_rows: list[dict[str, Any]] = []
    sources_rows: list[dict[str, Any]] = []

    if include_people:
        people_rows = [
            dict(row)
            for row in session.execute(
                text(
                    """
                    SELECT
                        id,
                        slug,
                        bucket,
                        COALESCE(image_url, '') AS image_url
                    FROM app.people_cards
                    ORDER BY id ASC
                    """
                )
            ).mappings()
        ]

    if include_sources:
        sources_rows = [
            dict(row)
            for row in session.execute(
                text(
                    """
                    SELECT
                        id,
                        slug,
                        bucket,
                        folder_prefix,
                        COALESCE(cover_media_url, '') AS cover_media_url
                    FROM app.sources_cards
                    ORDER BY id ASC
                    """
                )
            ).mappings()
        ]

    return people_rows, sources_rows


def _iter_limited(rows: list[dict[str, Any]], *, offset: int, limit: int | None) -> Iterable[dict[str, Any]]:
    sliced = rows[offset:] if offset > 0 else rows
    if limit is not None and limit >= 0:
        return sliced[:limit]
    return sliced


def _migrate_people(
    session,
    *,
    rows: list[dict[str, Any]],
    apply_changes: bool,
    commit_every: int,
    allow_external_downloads: bool,
    skip_already_target_size: bool,
    client,
    bucket_cache: dict[str, Any],
) -> tuple[Stats, list[dict[str, str]]]:
    stats = Stats()
    updates_preview: list[dict[str, str]] = []
    converted_cache: dict[tuple[str, str], tuple[str, int, int]] = {}

    for row in rows:
        stats.scanned += 1
        row_id = int(row["id"])
        slug = str(row.get("slug") or "").strip()
        current_url = str(row.get("image_url") or "").strip()
        row_bucket = _resolve_bucket_name(row.get("bucket"))

        if not current_url:
            stats.skipped_empty += 1
            continue
        if _is_placeholder_image(current_url):
            stats.skipped_placeholder += 1
            continue

        cache_key = (row_bucket, current_url)
        if cache_key in converted_cache:
            new_url, src_w, src_h = converted_cache[cache_key]
            stats.reused_conversions += 1
            if new_url != current_url:
                updates_preview.append(
                    {
                        "scope": "people",
                        "id": str(row_id),
                        "slug": slug,
                        "from": current_url,
                        "to": new_url,
                        "source_size": f"{src_w}x{src_h}",
                    }
                )
                if apply_changes:
                    session.execute(
                        text(
                            """
                            UPDATE app.people_cards
                            SET image_url = :image_url,
                                updated_at = now()
                            WHERE id = :id
                            """
                        ),
                        {"id": row_id, "image_url": new_url},
                    )
                    stats.updated_rows += 1
                    if commit_every > 0 and stats.updated_rows % commit_every == 0:
                        session.commit()
            continue

        try:
            payload, _content_type, source_blob, used_external = _download_bytes(
                url_value=current_url,
                bucket_value=row_bucket,
                allow_external_downloads=allow_external_downloads,
                client=client,
                bucket_cache=bucket_cache,
            )
        except Exception as exc:  # noqa: BLE001
            stats.errors += 1
            print(f"[people:{row_id}] download failed: {exc}")
            continue

        if payload is None:
            stats.skipped_unhandled_url += 1
            continue
        if used_external:
            stats.external_downloads += 1

        try:
            migrated_png, src_w, src_h = _transform_to_target_png(payload)
        except Exception as exc:  # noqa: BLE001
            stats.errors += 1
            print(f"[people:{row_id}] transform failed: {exc}")
            continue

        if skip_already_target_size and src_w == TARGET_WIDTH and src_h == TARGET_HEIGHT:
            stats.skipped_already_target += 1
            converted_cache[cache_key] = (current_url, src_w, src_h)
            continue

        target_blob = _target_blob_name(
            source_blob=source_blob,
            fallback_prefix=f"{DEFAULT_MEDIA_PREFIX}/{_slugify(slug)}",
            fallback_key=f"people-{row_id}-{slug}",
        )
        new_url = media_path(target_blob)

        if apply_changes:
            try:
                upload_bucket = bucket_cache.setdefault(row_bucket, client.bucket(row_bucket))
                upload_blob = upload_bucket.blob(target_blob)
                upload_blob.cache_control = "public, max-age=3600"
                upload_blob.upload_from_string(migrated_png, content_type="image/png")
            except Exception as exc:  # noqa: BLE001
                stats.errors += 1
                print(f"[people:{row_id}] upload failed: {exc}")
                continue

            try:
                session.execute(
                    text(
                        """
                        UPDATE app.people_cards
                        SET image_url = :image_url,
                            updated_at = now()
                        WHERE id = :id
                        """
                    ),
                    {"id": row_id, "image_url": new_url},
                )
                stats.updated_rows += 1
                if commit_every > 0 and stats.updated_rows % commit_every == 0:
                    session.commit()
            except Exception as exc:  # noqa: BLE001
                stats.errors += 1
                print(f"[people:{row_id}] update failed: {exc}")
                continue

        stats.migrated += 1
        converted_cache[cache_key] = (new_url, src_w, src_h)
        updates_preview.append(
            {
                "scope": "people",
                "id": str(row_id),
                "slug": slug,
                "from": current_url,
                "to": new_url,
                "source_size": f"{src_w}x{src_h}",
            }
        )

    return stats, updates_preview


def _migrate_sources(
    session,
    *,
    rows: list[dict[str, Any]],
    apply_changes: bool,
    commit_every: int,
    allow_external_downloads: bool,
    skip_already_target_size: bool,
    client,
    bucket_cache: dict[str, Any],
) -> tuple[Stats, list[dict[str, str]]]:
    stats = Stats()
    updates_preview: list[dict[str, str]] = []
    converted_cache: dict[tuple[str, str], tuple[str, int, int]] = {}

    for row in rows:
        stats.scanned += 1
        row_id = int(row["id"])
        slug = str(row.get("slug") or "").strip()
        folder_prefix = str(row.get("folder_prefix") or "").strip()
        current_url = str(row.get("cover_media_url") or "").strip()
        row_bucket = _resolve_bucket_name(row.get("bucket"))

        if not current_url:
            stats.skipped_empty += 1
            continue
        if _is_placeholder_image(current_url):
            stats.skipped_placeholder += 1
            continue

        cache_key = (row_bucket, current_url)
        if cache_key in converted_cache:
            new_url, src_w, src_h = converted_cache[cache_key]
            stats.reused_conversions += 1
            if new_url != current_url:
                updates_preview.append(
                    {
                        "scope": "sources",
                        "id": str(row_id),
                        "slug": slug,
                        "from": current_url,
                        "to": new_url,
                        "source_size": f"{src_w}x{src_h}",
                    }
                )
                if apply_changes:
                    session.execute(
                        text(
                            """
                            UPDATE app.sources_cards
                            SET cover_media_url = :cover_media_url,
                                updated_at = now()
                            WHERE id = :id
                            """
                        ),
                        {"id": row_id, "cover_media_url": new_url},
                    )
                    stats.updated_rows += 1
                    if commit_every > 0 and stats.updated_rows % commit_every == 0:
                        session.commit()
            continue

        try:
            payload, _content_type, source_blob, used_external = _download_bytes(
                url_value=current_url,
                bucket_value=row_bucket,
                allow_external_downloads=allow_external_downloads,
                client=client,
                bucket_cache=bucket_cache,
            )
        except Exception as exc:  # noqa: BLE001
            stats.errors += 1
            print(f"[sources:{row_id}] download failed: {exc}")
            continue

        if payload is None:
            stats.skipped_unhandled_url += 1
            continue
        if used_external:
            stats.external_downloads += 1

        try:
            migrated_png, src_w, src_h = _transform_to_target_png(payload)
        except Exception as exc:  # noqa: BLE001
            stats.errors += 1
            print(f"[sources:{row_id}] transform failed: {exc}")
            continue

        if skip_already_target_size and src_w == TARGET_WIDTH and src_h == TARGET_HEIGHT:
            stats.skipped_already_target += 1
            converted_cache[cache_key] = (current_url, src_w, src_h)
            continue

        fallback_prefix = folder_prefix or f"{DEFAULT_MEDIA_PREFIX}/sources/{_slugify(slug)}"
        target_blob = _target_blob_name(
            source_blob=source_blob,
            fallback_prefix=fallback_prefix,
            fallback_key=f"source-{row_id}-{slug}",
        )
        new_url = media_path(target_blob)

        if apply_changes:
            try:
                upload_bucket = bucket_cache.setdefault(row_bucket, client.bucket(row_bucket))
                upload_blob = upload_bucket.blob(target_blob)
                upload_blob.cache_control = "public, max-age=3600"
                upload_blob.upload_from_string(migrated_png, content_type="image/png")
            except Exception as exc:  # noqa: BLE001
                stats.errors += 1
                print(f"[sources:{row_id}] upload failed: {exc}")
                continue

            try:
                session.execute(
                    text(
                        """
                        UPDATE app.sources_cards
                        SET cover_media_url = :cover_media_url,
                            updated_at = now()
                        WHERE id = :id
                        """
                    ),
                    {"id": row_id, "cover_media_url": new_url},
                )
                stats.updated_rows += 1
                if commit_every > 0 and stats.updated_rows % commit_every == 0:
                    session.commit()
            except Exception as exc:  # noqa: BLE001
                stats.errors += 1
                print(f"[sources:{row_id}] update failed: {exc}")
                continue

        stats.migrated += 1
        converted_cache[cache_key] = (new_url, src_w, src_h)
        updates_preview.append(
            {
                "scope": "sources",
                "id": str(row_id),
                "slug": slug,
                "from": current_url,
                "to": new_url,
                "source_size": f"{src_w}x{src_h}",
            }
        )

    return stats, updates_preview


def _print_stats(label: str, stats: Stats) -> None:
    print(f"\n[{label}]")
    for key, value in stats.as_dict().items():
        print(f"  {key}: {value}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Migrate people/source card images to 360x270 and update stored URLs.",
    )
    parser.add_argument("--apply", action="store_true", help="Apply uploads and DB updates. Without this, runs dry-run only.")
    parser.add_argument("--env-file", default="", help="Optional .env file to load before connecting to DB/storage.")
    parser.add_argument("--people-only", action="store_true", help="Migrate only app.people_cards.image_url.")
    parser.add_argument("--sources-only", action="store_true", help="Migrate only app.sources_cards.cover_media_url.")
    parser.add_argument("--limit", type=int, default=None, help="Limit rows per scope (after offset).")
    parser.add_argument("--offset", type=int, default=0, help="Offset rows per scope.")
    parser.add_argument("--commit-every", type=int, default=25, help="Commit every N updated rows when --apply is enabled.")
    parser.add_argument(
        "--allow-external-downloads",
        action="store_true",
        help="Allow fetching non-/media HTTP(S) image URLs for migration.",
    )
    parser.add_argument(
        "--force-reprocess-target-size",
        action="store_true",
        help="Reprocess images even if already 360x270.",
    )
    parser.add_argument("--report-file", default="", help="Optional path to write JSON report.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    env_file = str(args.env_file or "").strip()
    if env_file:
        load_dotenv(env_file, override=True)
        print(f"Loaded env file: {env_file}")
    else:
        load_dotenv(override=False)

    include_people = not args.sources_only
    include_sources = not args.people_only

    if not include_people and not include_sources:
        print("Nothing selected: use default, --people-only, or --sources-only.")
        return 2

    apply_changes = bool(args.apply)
    skip_already_target_size = not bool(args.force_reprocess_target_size)

    client = storage_client()
    bucket_cache: dict[str, Any] = {}

    with session_scope() as session:
        people_rows_all, sources_rows_all = _load_rows(
            session,
            include_people=include_people,
            include_sources=include_sources,
        )

        people_rows = list(_iter_limited(people_rows_all, offset=max(0, int(args.offset)), limit=args.limit))
        sources_rows = list(_iter_limited(sources_rows_all, offset=max(0, int(args.offset)), limit=args.limit))

        print("Migration mode:", "APPLY" if apply_changes else "DRY-RUN")
        print(f"People rows selected: {len(people_rows)}")
        print(f"Sources rows selected: {len(sources_rows)}")

        people_stats = Stats()
        sources_stats = Stats()
        people_updates: list[dict[str, str]] = []
        sources_updates: list[dict[str, str]] = []

        if include_people:
            people_stats, people_updates = _migrate_people(
                session,
                rows=people_rows,
                apply_changes=apply_changes,
                commit_every=max(0, int(args.commit_every)),
                allow_external_downloads=bool(args.allow_external_downloads),
                skip_already_target_size=skip_already_target_size,
                client=client,
                bucket_cache=bucket_cache,
            )

        if include_sources:
            sources_stats, sources_updates = _migrate_sources(
                session,
                rows=sources_rows,
                apply_changes=apply_changes,
                commit_every=max(0, int(args.commit_every)),
                allow_external_downloads=bool(args.allow_external_downloads),
                skip_already_target_size=skip_already_target_size,
                client=client,
                bucket_cache=bucket_cache,
            )

        # Ensure final commit after batched commits.
        if apply_changes:
            session.commit()

    _print_stats("people", people_stats)
    _print_stats("sources", sources_stats)

    total_updates = people_stats.updated_rows + sources_stats.updated_rows
    total_migrated = people_stats.migrated + sources_stats.migrated
    print("\nTotals:")
    print(f"  migrated_images: {total_migrated}")
    print(f"  updated_rows: {total_updates}")

    sample_updates = (people_updates + sources_updates)[:20]
    if sample_updates:
        print("\nSample updates (up to 20):")
        for item in sample_updates:
            print(
                f"  [{item['scope']}] id={item['id']} slug={item['slug']} "
                f"{item['source_size']} :: {item['from']} -> {item['to']}"
            )

    if args.report_file:
        report_path = Path(str(args.report_file)).expanduser()
        report_payload = {
            "mode": "apply" if apply_changes else "dry-run",
            "target_size": {"width": TARGET_WIDTH, "height": TARGET_HEIGHT},
            "people": people_stats.as_dict(),
            "sources": sources_stats.as_dict(),
            "sample_updates": sample_updates,
        }
        report_path.write_text(json.dumps(report_payload, indent=2), encoding="utf-8")
        print(f"\nWrote report: {report_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
