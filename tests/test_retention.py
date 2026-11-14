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
