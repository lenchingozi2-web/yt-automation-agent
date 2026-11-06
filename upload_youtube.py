"""Retry saved Studio uploads without generating or rendering another video."""
from worker import main

if __name__ == "__main__":
    main(["--publish-only", "--max-jobs", "3"])
