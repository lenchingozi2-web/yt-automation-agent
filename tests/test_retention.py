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
