from datetime import datetime, timedelta, timezone
from studio_maintenance import expiration_flags
now = datetime(2026, 11, 3, tzinfo=timezone.utc)
old = (now - timedelta(days=100)).isoformat()
published = {"id": "video-id", "status": "completed", "created_at": old,
             "youtube_url": "https://www.youtube.com/watch?v=example123",
             "video_url": "https://x.supabase.co/storage/v1/object/public/renders/video-id.mp4",
             "thumbnail_url": "https://x.supabase.co/storage/v1/object/public/thumbnails/video-id.jpg"}
assert expiration_flags(published, now) == (True, True)
assert expiration_flags({**published, "youtube_url": ""}, now) == (False, False)
assert expiration_flags({**published, "completed_at": (now - timedelta(days=2)).isoformat()}, now) == (False, False)
assert expiration_flags({**published, "created_at": now.isoformat()}, now) == (False, False)
print("Retention safety tests passed")
