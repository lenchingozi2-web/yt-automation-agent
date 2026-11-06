"""Compatibility CLI: unattended generation uses the shared Studio queue."""
from studio_automation import main as queue_video
from worker import main as render_queue

if __name__ == "__main__":
    queue_video()
    render_queue(["--drain", "--max-jobs", "3"])
