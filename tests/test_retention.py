from datetime import datetime, timedelta, timezone

from studio_maintenance import attention_action, expiration_flags

now = datetime(2026, 11, 3, tzinfo=timezone.utc)
old = (now - timedelta(days=100)).isoformat()
exact_cutoff = (now - timedelta(hours=12)).isoformat()
before_cutoff = (now - timedelta(hours=12) + timedelta(seconds=1)).isoformat()
published = {"id": "video-id", "status": "completed", "created_at": old, "published_at": old,
             "youtube_url": "https://www.youtube.com/watch?v=example1234",
             "video_url": "https://x.supabase.co/storage/v1/object/public/renders/video-id.mp4",
             "thumbnail_url": "https://x.supabase.co/storage/v1/object/public/thumbnails/video-id.jpg"}

assert expiration_flags(published, now) == (True, True)
assert expiration_flags({**published, "youtube_url": ""}, now) == (False, False)
assert expiration_flags({**published, "published_at": before_cutoff}, now) == (False, False)
assert expiration_flags({**published, "published_at": exact_cutoff}, now) == (True, False)
assert expiration_flags({**published, "thumbnail_publish_status": "retry"}, now) == (True, False)
assert expiration_flags({**published, "status": "rendering"}, now) == (False, False)
assert expiration_flags({**published, "published_at": None}, now) == (False, True)
assert expiration_flags({**published, "published_at": None, "upload_confirmed_observed_at": exact_cutoff}, now) == (True, False)
assert expiration_flags({**published, "publish_at": (now + timedelta(days=20)).isoformat()}, now) == (True, False)
assert expiration_flags({**published, "preview_expired_at": now.isoformat()}, now) == (False, True)

assert attention_action({"status": "failed"}) == "delete"
for status in ("upload_uncertain", "failed", "auth_required", "not_configured", "retry"):
    assert attention_action({"status": "completed", "scenes": [{"_studio": {"publish_status": status}}]}) == "delete"
for status in ("awaiting_review", "pending", "uploading", "uploaded"):
    assert attention_action({"status": "completed", "publish_status": status}) is None
assert attention_action({"status": "rendering", "publish_status": "failed"}) is None
assert attention_action({**published, "publish_status": "failed"}) is None
assert attention_action({**published, "thumbnail_publish_status": "failed"}) == "dismiss_thumbnail"
print("Retention and queue cleanup safety tests passed")

# Deleting a Studio record must eventually remove only its orphaned media.
from unittest.mock import Mock, patch
from studio_maintenance import cleanup_orphan_media

existing_id = "11111111-1111-4111-8111-111111111111"
orphan_id = "22222222-2222-4222-8222-222222222222"
objects = {
    "renders": [
        {"id": "a", "name": orphan_id + ".mp4", "created_at": old, "metadata": {"size": 1024}},
        {"id": "b", "name": existing_id + ".mp4", "created_at": old, "metadata": {"size": 2048}},
        {"id": "c", "name": "random.mp4", "created_at": old, "metadata": {"size": 2048}},
        {"id": "d", "name": orphan_id + "-pace-v1.mp4", "created_at": now.isoformat(), "metadata": {"size": 2048}},
    ],
    "thumbnails": [],
    "studio-masters": [
        {"id": "e", "name": orphan_id + "-" + "a" * 64 + "-000.part",
         "created_at": old, "metadata": {"size": 4096}},
    ],
}
deleted_files = []

class Bucket:
    def __init__(self, name):
        self.name = name
    def remove(self, paths):
        deleted_files.extend((self.name, path) for path in paths)
        objects[self.name] = [item for item in objects[self.name] if item["name"] not in paths]
        return paths

class Query:
    def select(self, *args): return self
    def order(self, *args): return self
    def range(self, *args): return self
    def execute(self): return type("Response", (), {"data": [{"id": existing_id}]})()

fake_client = Mock()
fake_client.table.return_value = Query()
fake_client.storage.from_.side_effect = Bucket
with patch("studio_master.media_objects", side_effect=lambda client, bucket: list(objects[bucket])):
    cleanup_orphan_media(fake_client, now, False, has_master_bucket=True)
assert sorted(deleted_files) == sorted([
    ("renders", orphan_id + ".mp4"),
    ("studio-masters", orphan_id + "-" + "a" * 64 + "-000.part"),
])
assert len(objects["renders"]) == 3
print("Orphaned Studio media cleanup tests passed")

assert expiration_flags({**published, "video_url": "", "upload_master": {"parts": ["example"]}}, now) == (True, True)
assert expiration_flags({**published, "video_url": "", "upload_master": {"parts": ["example"]}, "preview_expired_at": now.isoformat()}, now) == (False, True)


# Verify the compact worker probe has exactly the same eligibility behavior as
# full rows, including retries, legacy fallback, and no publishing without approval.
from urllib.parse import parse_qs
from studio_publish import completed_probe_rows, publication_due, thumbnail_due, _PROBE_FIELDS

probe_meta = {"duration_seconds": 60, "publish_approved_at": old, "publish_approved_by": "owner",
              "video_url": "https://example.invalid/render.mp4", "publish_status": "pending"}
probe_variants = [
    probe_meta,
    {**probe_meta, "publish_approved_at": None},
    {**probe_meta, "publish_status": "uploading", "publish_heartbeat_at": now.isoformat()},
    {**probe_meta, "publish_status": "auth_required", "publish_auth_version": "fixture-version"},
    {**probe_meta, "publish_status": "uploaded", "thumbnail_url": "https://example.invalid/preview.jpg",
        "thumbnail_publish_status": "retry", "youtube_id": "example1234"},
    {**probe_meta, "publish_retry_at": (now + timedelta(hours=2)).isoformat()},
    {**probe_meta, "discard_requested_at": old},
    {**probe_meta, "publish_attempts": 5},
]
full = [{"id": str(i), "status": "completed", "youtube_video_id": None,
         "scenes": [{"_studio": meta}, {"narration": "Metadata not needed for probing."}]}
        for i, meta in enumerate(probe_variants)]
projected = [{"id": row["id"], "status": row["status"], "youtube_video_id": row["youtube_video_id"],
             "probe_marker": row["scenes"][0]["_studio"]["duration_seconds"],
             **{f"meta_{field}": row["scenes"][0]["_studio"].get(field) for field in _PROBE_FIELDS}}
            for row in full]
calls = []
def probe_read(query):
    selection = parse_qs(query)["select"][0]
    calls.append(selection)
    return full if selection == "*" else projected

compact = completed_probe_rows(probe_read)
assert len(calls) == 1 and calls[0] != "*"
for baseline, compact_row in zip(full, compact):
    assert publication_due(baseline, "fixture-version", now) == publication_due(compact_row, "fixture-version", now)
    assert thumbnail_due(baseline, "fixture-version", now) == thumbnail_due(compact_row, "fixture-version", now)
projected[0]["probe_marker"] = None
assert completed_probe_rows(probe_read) == full
assert calls[-1] == "*"
print("Compact probe preserves publication and thumbnail eligibility.")
