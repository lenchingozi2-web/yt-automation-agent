import os
import json
from pathlib import Path
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

WORK_DIR = Path("output_run")


def upload_to_youtube():
    token_json = os.getenv("YOUTUBE_TOKEN_JSON")
    if not token_json:
        print("[i] YOUTUBE_TOKEN_JSON not set. Skipping automatic YouTube upload (video saved in artifacts).")
        return

    script_data = json.loads((WORK_DIR / "script.json").read_text())
    video_path = WORK_DIR / "final_video.mp4"

    creds_info = json.loads(token_json)
    creds = Credentials.from_authorized_user_info(creds_info, ["https://www.googleapis.com/auth/youtube.upload"])
    youtube = build("youtube", "v3", credentials=creds)

    body = {
        "snippet": {
            "title": script_data["title"][:100],
            "description": script_data["description"],
            "tags": script_data.get("tags", []),
            "categoryId": "27",
        },
        "status": {
            "privacyStatus": "unlisted",
            "selfDeclaredMadeForKids": False,
        },
    }

    media = MediaFileUpload(str(video_path), chunksize=-1, resumable=True, mimetype="video/mp4")
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    response = request.execute()
    print(f"[✓] Uploaded to YouTube (Unlisted): https://youtu.be/{response['id']}")


if __name__ == "__main__":
    upload_to_youtube()
