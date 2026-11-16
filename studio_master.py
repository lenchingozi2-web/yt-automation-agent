"""Keep a private upload master when it exceeds the single-object preview limit."""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path

from studio_retry import retry_io

MASTER_BUCKET = "studio-masters"
OBJECT_LIMIT = 48_000_000
PART_BYTES = 32_000_000
STORAGE_BUDGET = 800 * 1024 * 1024


def bucket_exists(client, name: str) -> bool:
    from storage3.exceptions import StorageApiError

    try:
        retry_io(lambda: client.storage.get_bucket(name), "check Studio media bucket")
    except StorageApiError as error:
        if str(error.status) == "404" or "bucket not found" in str(error.message).lower() or error.code == "NoSuchBucket":
            return False
        raise
    return True


def media_objects(client, name: str, *, search: str | None = None):
    bucket = client.storage.from_(name)
    options = {"limit": 200, "sortBy": {"column": "name", "order": "asc"}}
    if search:
        options["search"] = search
    while True:
        batch = retry_io(lambda: bucket.list("", options), "list Studio media")
        yield from (item for item in batch if item.get("id"))
        if len(batch) < options["limit"]:
            break
        options["offset"] = options.get("offset", 0) + len(batch)


def part_path(video_id: str, digest: str, index: int) -> str:
    return f"{video_id}-{digest}-{index:03d}.part"


def check_media_budget(client, video_id: str, *, required: int, preview_bucket: str, preview_name: str,
                       master_digest: str = "", has_master_bucket: bool) -> None:
    used = sum(int((item.get("metadata") or {}).get("size") or 0)
               for item in media_objects(client, preview_bucket) if item["name"] != preview_name)
    if has_master_bucket:
        current_master = f"{video_id}-{master_digest}-"
        used += sum(int((item.get("metadata") or {}).get("size") or 0)
                    for item in media_objects(client, MASTER_BUCKET)
                    if not master_digest or not item["name"].startswith(current_master))
    if used + required > STORAGE_BUDGET:
        raise ValueError("Studio media storage is nearly full. Clear expired media before rendering this video.")



def preflight_long_video_storage(client, video_id: str, duration_seconds: float, preview_bucket: str = "renders") -> None:
    """Reject only clearly impossible HD capacity, before costly long narration.

    This is a lower bound, not a promise that final output will fit. The exact
    byte total is still verified by save_upload_master after rendering.
    """
    if not 600 <= duration_seconds <= 1800:
        return
    # At even an extremely restrained 850 kb/s + one browser preview, a
    # continuous 1080p long video cannot fit below this storage floor.
    minimum_bytes = int(duration_seconds * 850_000 / 8) + min(46_000_000, int(duration_seconds * 30_000))
    try:
        has_master = bucket_exists(client, MASTER_BUCKET)
        used = sum(int((item.get("metadata") or {}).get("size") or 0)
                   for item in media_objects(client, preview_bucket)
                   if item.get("name") != f"{video_id}.mp4")
        if has_master:
            used += sum(int((item.get("metadata") or {}).get("size") or 0)
                        for item in media_objects(client, MASTER_BUCKET)
                        if not str(item.get("name", "")).startswith(video_id + "-"))
        available = max(0, STORAGE_BUDGET - used)
    except Exception as error:
        print(f"[storage] preliminary capacity check unavailable: {type(error).__name__}", flush=True)
        return
    print(f"[storage] long-form preliminary budget: {available // (1024 * 1024)} MiB free; "
          f"absolute minimum {minimum_bytes // (1024 * 1024)} MiB "
          f"for {duration_seconds / 60:.1f} minutes", flush=True)
    if available < minimum_bytes:
        raise ValueError(
            f"Not enough Studio storage for a {duration_seconds / 60:.0f}-minute full-quality video. "
            f"Only {available // (1024 * 1024)} MiB is free. "
            f"At least {minimum_bytes // (1024 * 1024)} MiB is needed before narration begins. "
            "Delete unused videos or confirmed-expired media; no video quality was reduced."
        )


def reclaim_expired_uploaded_media(client, video_id: str) -> int:
    """Safely reclaim only confirmed YouTube uploads past the retention window.

    On-demand cleanup uses the same policy as nightly retention and never touches
    unapproved videos or the source of an active revision.
    """
    from studio_maintenance import clean_row, expiration_flags, metadata, video_rows

    now = datetime.now(timezone.utc)
    pinned = set()
    try:
        for item in video_rows(client, ["pending", "rendering"]):
            revision = metadata(item).get("revision")
            if isinstance(revision, dict) and revision.get("original_media"):
                pinned.update(str(revision.get(key)) for key in ("parent_id", "root_id") if revision.get(key))
        reclaimed = 0
        for row in video_rows(client, ["completed"]):
            if str(row.get("id")) == video_id or str(row.get("id")) in pinned:
                continue
            archive_video, _ = expiration_flags(row, now)
            if not archive_video:
                continue
            try:
                outcome = clean_row(client, row, now)
                reclaimed += bool(outcome["video"])
            except Exception as error:
                print(f"[storage] expired-upload cleanup skipped {row.get('id')}: {type(error).__name__}", flush=True)
        if reclaimed:
            print(f"[storage] reclaimed stored copies of {reclaimed} expired YouTube uploads", flush=True)
        return reclaimed
    except Exception as error:
        print(f"[storage] retention probe unavailable: {type(error).__name__}", flush=True)
        return 0


def save_upload_master(client, video_id: str, master: Path, preview: Path, preview_bucket: str, preview_name: str):
    digest = ""
    if preview != master:
        with master.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
    has_bucket = bucket_exists(client, MASTER_BUCKET)
    preview_bytes = preview.stat().st_size
    master_bytes = master.stat().st_size if digest else 0
    try:
        check_media_budget(client, video_id, required=preview_bytes + master_bytes, preview_bucket=preview_bucket,
                           preview_name=preview_name, master_digest=digest, has_master_bucket=has_bucket)
    except ValueError:
        # Free only media whose YouTube upload was confirmed at least 12 hours ago.
        reclaim_expired_uploaded_media(client, video_id)
        try:
            check_media_budget(client, video_id, required=preview_bytes + master_bytes, preview_bucket=preview_bucket,
                               preview_name=preview_name, master_digest=digest, has_master_bucket=has_bucket)
        except ValueError:
            raise
    if not digest:
        return None
    if not has_bucket:
        from storage3.exceptions import StorageApiError

        try:
            client.storage.create_bucket(MASTER_BUCKET, options={"public": False})
        except StorageApiError:
            # Another producer may have created the bucket while this render was finishing.
            if not bucket_exists(client, MASTER_BUCKET):
                raise
    bucket = client.storage.from_(MASTER_BUCKET)
    paths = []
    try:
        with master.open("rb") as source:
            while chunk := source.read(PART_BYTES):
                path = part_path(video_id, digest, len(paths))
                paths.append(path)
                retry_io(lambda: bucket.upload(path, chunk, file_options={
                    "content-type": "application/octet-stream", "upsert": "true",
                }), "save full-resolution Studio upload")
    except Exception:
        try:
            retry_io(lambda: bucket.remove(paths), "remove incomplete Studio upload master")
        except Exception:
            print(f"[storage] could not remove incomplete master for {video_id}", flush=True)
        raise
    print(f"[storage] saved {master_bytes:,} byte full-resolution master in {len(paths)} parts", flush=True)
    return {"sha256": digest, "bytes": master_bytes, "parts": len(paths)}


def download_upload_master(client, video_id: str, manifest: dict, target: Path) -> None:
    if not isinstance(manifest, dict):
        raise ValueError("Invalid Studio upload master metadata")
    digest, size, count = (manifest.get(key) for key in ("sha256", "bytes", "parts"))
    if (not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)
            or type(size) is not int or not 0 < size <= STORAGE_BUDGET
            or type(count) is not int or count != (size + PART_BYTES - 1) // PART_BYTES):
        raise ValueError("Invalid Studio upload master metadata")
    bucket = client.storage.from_(MASTER_BUCKET)
    temporary = target.with_suffix(".part")
    checksum = hashlib.sha256()
    try:
        with temporary.open("wb") as output:
            for index in range(count):
                filename = part_path(video_id, digest, index)
                chunk = retry_io(lambda: bucket.download(filename), "download full-resolution Studio upload")
                if len(chunk) != min(PART_BYTES, size - index * PART_BYTES):
                    raise ValueError("A full-resolution upload part has an unexpected size")
                checksum.update(chunk)
                output.write(chunk)
        if checksum.hexdigest() != digest:
            raise ValueError("The full-resolution Studio upload failed its checksum")
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def remove_upload_master(client, video_id: str, *, has_bucket: bool | None = None) -> int:
    if has_bucket is None:
        has_bucket = bucket_exists(client, MASTER_BUCKET)
    if not has_bucket:
        return 0
    prefix = f"{video_id}-"
    paths = [item["name"] for item in media_objects(client, MASTER_BUCKET, search=prefix)
             if item["name"].startswith(prefix)]
    if not paths:
        return 0
    bucket = client.storage.from_(MASTER_BUCKET)
    return len(retry_io(lambda: bucket.remove(paths), "remove full-resolution Studio upload") or [])
