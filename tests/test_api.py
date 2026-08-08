"""Integration tests for FastAPI endpoints."""

import io
import pytest
from fastapi.testclient import TestClient

from youtube_uploader.app import app

client = TestClient(app)


def test_health_check_endpoint():
    """Test GET /health returns 200 OK and valid status/version."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "version" in data


def test_upload_endpoint_mock_mode():
    """Test POST /upload with a dummy file in mock mode."""
    dummy_file_content = b"fake video binary stream content"
    files = {"file": ("test_clip.mp4", io.BytesIO(dummy_file_content), "video/mp4")}
    data = {
        "title": "API Test Video",
        "description": "Uploaded via FastAPI test client",
        "privacy_status": "private",
        "tags": "fastapi,test,api",
        "mock": "true",
    }

    response = client.post("/upload", files=files, data=data)
    assert response.status_code == 201
    res_data = response.json()

    assert res_data["status"] == "uploaded"
    assert res_data["mock"] is True
    assert "video_id" in res_data
    assert res_data["youtube_url"].startswith("https://youtu.be/")
    assert res_data["title"] == "API Test Video"
    assert res_data["privacy_status"] == "private"
    assert res_data["tags"] == ["fastapi", "test", "api"]


def test_upload_endpoint_invalid_privacy():
    """Test POST /upload with invalid privacy status returns 400 Bad Request."""
    dummy_file_content = b"fake video content"
    files = {"file": ("test.mp4", io.BytesIO(dummy_file_content), "video/mp4")}
    data = {
        "privacy_status": "super_secret",
        "mock": "true",
    }

    response = client.post("/upload", files=files, data=data)
    assert response.status_code == 400
    assert "Invalid privacy status" in response.json()["detail"]
