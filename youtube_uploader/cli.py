"""Command-line interface (CLI) for YouTube Video Uploader."""

import sys
import argparse
import logging
from typing import List, Optional

from youtube_uploader.upload import upload_video

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def parse_args(args: Optional[List[str]] = None) -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="CLI tool to upload videos to YouTube via API v3."
    )
    parser.add_argument("file_path", nargs="?", help="Path to video file to upload")
    parser.add_argument("--login", action="store_true", help="Perform Google OAuth login and save token.json")
    parser.add_argument("--title", "-t", help="Video title (defaults to filename)")
    parser.add_argument("--description", "-d", default="", help="Video description text")
    parser.add_argument(
        "--privacy",
        "-p",
        choices=["private", "unlisted", "public"],
        default="private",
        help="Privacy status (default: private)",
    )
    parser.add_argument("--category", "-c", default="22", help="YouTube Category ID (default: 22)")
    parser.add_argument("--tags", nargs="*", default=[], help="Video tags space-separated")
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Run upload in mock mode without calling live YouTube API",
    )

    return parser.parse_args(args)


def main():
    """Main CLI entrypoint."""
    parsed = parse_args()

    if parsed.login:
        from youtube_uploader.auth import login_cli
        print("Starting Google OAuth login flow...")
        login_cli()
        print("✓ Successfully authenticated! Credentials saved to token.json.")
        return

    if not parsed.file_path:
        print("Error: file_path argument is required unless using --login.")
        sys.exit(1)

    try:
        result = upload_video(
            file_path=parsed.file_path,
            title=parsed.title,
            description=parsed.description,
            privacy_status=parsed.privacy,
            category_id=parsed.category,
            tags=parsed.tags,
            mock_mode=parsed.mock,
        )

        print("\n==========================================")
        print(" SUCCESSFUL UPLOAD")
        print("==========================================")
        print(f"Status:      {result['status']}")
        print(f"Video ID:    {result['video_id']}")
        print(f"YouTube URL: {result['youtube_url']}")
        print(f"Privacy:     {result['privacy_status']}")
        print("==========================================\n")
    except Exception as err:
        logger.error("CLI upload error: %s", err)
        sys.exit(1)


if __name__ == "__main__":
    main()
