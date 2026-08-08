"""YouTube video upload core logic using YouTube Data API v3."""

import os
import time
import logging
from typing import Optional, Dict, Any, List
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError

from youtube_uploader.config import settings
from youtube_uploader.auth import get_youtube_client

logger = logging.getLogger(__name__)

# Standard YouTube URL format
YOUTUBE_WATCH_URL = "https://youtu.be/{video_id}"


def upload_video(
    file_path: str,
    title: Optional[str] = None,
    description: Optional[str] = "",
    privacy_status: Optional[str] = None,
    category_id: Optional[str] = None,
    tags: Optional[List[str]] = None,
    youtube_client: Optional[Any] = None,
    mock_mode: bool = False,
) -> Dict[str, Any]:
    """Upload a video file to YouTube using resumable chunked upload.

    Args:
        file_path: Absolute or relative path to the video file.
        title: Title for YouTube video. Defaults to the filename if omitted.
        description: Video description text.
        privacy_status: 'private', 'unlisted', or 'public'. Defaults to settings.
        category_id: YouTube video category ID (numeric string, e.g., '22').
        tags: Optional list of keyword tags.
        youtube_client: Optional YouTube service resource (built automatically if None).
        mock_mode: If True, simulates upload without calling real API.

    Returns:
        Dict[str, Any]: Upload summary containing status, video_id, and youtube_url.

    Raises:
        FileNotFoundError: If the input video file does not exist.
        ValueError: If parameters are invalid.
        RuntimeError: If upload fails after retries.
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Video file not found at path: {file_path}")

    # Fallbacks for optional fields
    file_basename = os.path.basename(file_path)
    video_title = title or os.path.splitext(file_basename)[0]
    privacy = privacy_status or settings.default_privacy_status
    category = category_id or settings.default_category_id
    video_tags = tags or []

    if privacy not in ("private", "unlisted", "public"):
        raise ValueError(f"Invalid privacy status '{privacy}'. Must be 'private', 'unlisted', or 'public'.")

    # Handle Mock / Dry-Run Mode
    if mock_mode or settings.mock_youtube_api:
        logger.info("Executing video upload in MOCK mode for file: %s", file_path)
        fake_id = f"mock_vid_{int(time.time())}"
        return {
            "status": "uploaded",
            "mock": True,
            "video_id": fake_id,
            "youtube_url": YOUTUBE_WATCH_URL.format(video_id=fake_id),
            "title": video_title,
            "privacy_status": privacy,
            "category_id": category,
            "tags": video_tags,
        }

    # Initialize client if not provided
    if youtube_client is None:
        youtube_client = get_youtube_client()

    body: Dict[str, Any] = {
        "snippet": {
            "title": video_title,
            "description": description or "",
            "tags": video_tags,
            "categoryId": category,
        },
        "status": {
            "privacyStatus": privacy,
        },
    }

    # Prepare chunked media upload (chunk size must be multiple of 256KB)
    chunk_size = settings.chunk_size_mb * 1024 * 1024
    media = MediaFileUpload(
        file_path,
        chunksize=chunk_size,
        resumable=True,
        mimetype="video/*",
    )

    insert_request = youtube_client.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media,
    )

    logger.info("Starting upload for '%s' (Privacy: %s)...", video_title, privacy)
    response = None
    error = None
    retry_count = 0
    max_retries = 5

    while response is None:
        try:
            status, response = insert_request.next_chunk()
            if status:
                progress = int(status.progress() * 100)
                logger.info("Uploaded %d%%...", progress)
        except HttpError as err:
            if err.resp.status in [500, 502, 503, 504]:
                retry_count += 1
                if retry_count > max_retries:
                    raise RuntimeError(f"Upload failed after {max_retries} retries: {err}") from err
                sleep_sec = 2 ** retry_count
                logger.warning("Transient error %s. Retrying in %ds...", err.resp.status, sleep_sec)
                time.sleep(sleep_sec)
            else:
                raise RuntimeError(f"YouTube API HttpError during upload: {err}") from err
        except Exception as exc:
            raise RuntimeError(f"Unexpected error during upload chunk transfer: {exc}") from exc

    video_id = response.get("id")
    if not video_id:
        raise RuntimeError(f"Upload completed but no video ID was returned by YouTube API: {response}")

    youtube_url = YOUTUBE_WATCH_URL.format(video_id=video_id)
    logger.info("Upload successful! Video ID: %s | URL: %s", video_id, youtube_url)

    return {
        "status": "uploaded",
        "mock": False,
        "video_id": video_id,
        "youtube_url": youtube_url,
        "title": video_title,
        "privacy_status": privacy,
        "category_id": category,
        "tags": video_tags,
    }
