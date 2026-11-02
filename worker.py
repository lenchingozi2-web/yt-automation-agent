import os
import time
from dotenv import load_dotenv
from supabase import create_client, Client

# Load environment variables from the Next.js folder
load_dotenv('lenxiq-studio/.env.local')

url: str = os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
key: str = os.environ.get("NEXT_PUBLIC_SUPABASE_ANON_KEY")
supabase: Client = create_client(url, key)

def process_video(video):
    print(f"\n🚀 STARTED RENDERING: {video['title']}")
    print("Step 1: Generating Audio with Kokoro... (Pending)")
    print("Step 2: Stitching Media with FFmpeg... (Pending)")
    
    time.sleep(3) # Simulating render time
    
    # Update status to completed
    supabase.table("videos").update({"status": "completed"}).eq("id", video['id']).execute()
    print("✅ RENDER COMPLETE")

def listen_for_jobs():
    print("🎧 Python Engine listening for rendering jobs...")
    while True:
        try:
            # Check for videos where status is 'rendering'
            response = supabase.table("videos").select("*").eq("status", "rendering").execute()
            
            if response.data:
                for video in response.data:
                    process_video(video)
            
        except Exception as e:
            print(f"Error checking jobs: {e}")
            
        time.sleep(5) # Poll every 5 seconds

if __name__ == "__main__":
    listen_for_jobs()