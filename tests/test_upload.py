"""Unit tests for youtube_uploader core upload logic."""

import os
import tempfile
import pytest
from unittest.mock import MagicMock

from youtube_uploader.upload import upload_video, YOUTUBE_WATCH_URL


@pytest.fixture
def dummy_video_file():
    """Create a temporary dummy video file for testing."""
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
        tmp.write(b"dummy video data stream")
        tmp_path = tmp.name

    yield tmp_path

    if os.path.exists(tmp_path):
        os.remove(tmp_path)


def test_upload_non_existent_file():
    """Ensure upload_video raises FileNotFoundError when video file missing."""
    with pytest.raises(FileNotFoundError):
        upload_video("non_existent_file_xyz.mp4")


def test_upload_invalid_privacy_status(dummy_video_file):
    """Ensure upload_video raises ValueError for invalid privacy status."""
    with pytest.raises(ValueError, match="Invalid privacy status"):
        upload_video(dummy_video_file, privacy_status="invalid_status")


def test_upload_mock_mode(dummy_video_file):
    """Ensure upload_video returns correct output dictionary in mock mode."""
    result = upload_video(
        file_path=dummy_video_file,
        title="Test Video Title",
        description="Test description",
        privacy_status="private",
        tags=["test", "unit"],
        mock_mode=True,
    )

    assert result["status"] == "uploaded"
    assert result["mock"] is True
    assert "video_id" in result
    assert result["youtube_url"] == YOUTUBE_WATCH_URL.format(video_id=result["video_id"])
    assert result["title"] == "Test Video Title"
    assert result["privacy_status"] == "private"
    assert result["tags"] == ["test", "unit"]


def test_upload_with_mock_youtube_client(dummy_video_file):
    """Test upload_video using a mocked YouTube API client."""
    mock_client = MagicMock()
    mock_videos = MagicMock()
    mock_insert = MagicMock()

    mock_client.videos.return_value = mock_videos
    mock_videos.insert.return_value = mock_insert

    # Mock chunk response: first call returns progress, second call completes with response
    mock_status_progress = MagicMock()
    mock_status_progress.progress.return_value = 1.0

    mock_insert.next_chunk.return_value = (mock_status_progress, {"id": "test_youtube_id_123"})

    result = upload_video(
        file_path=dummy_video_file,
        title="Mocked API Video",
        privacy_status="unlisted",
        youtube_client=mock_client,
        mock_mode=False,
    )

    assert result["status"] == "uploaded"
    assert result["mock"] is False
    assert result["video_id"] == "test_youtube_id_123"
    assert result["youtube_url"] == YOUTUBE_WATCH_URL.format(video_id="test_youtube_id_123")
    assert result["title"] == "Mocked API Video"
    assert result["privacy_status"] == "unlisted"


def test_get_youtube_api_key_client():
    """Ensure get_youtube_api_key_client initializes client when API key is provided."""
    from youtube_uploader.auth import get_youtube_api_key_client
    client = get_youtube_api_key_client(api_key="AIzaSyTestApiKeyPlaceholder")
    assert client is not None

