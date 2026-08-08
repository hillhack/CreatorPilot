"""FastAPI application providing YouTube video uploading endpoints."""

import os
import shutil
import tempfile
import logging
from typing import Optional, List
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, status
from pydantic import BaseModel, Field

from youtube_uploader import __version__
from youtube_uploader.config import settings
from youtube_uploader.upload import upload_video

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(
    title="YouTube Video Uploader API",
    description="Automated video publishing service using YouTube Data API v3",
    version=__version__,
)


class UploadResponse(BaseModel):
    """Response model for video upload endpoint."""

    status: str = Field(..., json_schema_extra={"example": "uploaded"})
    mock: bool = Field(False, json_schema_extra={"example": False})
    video_id: str = Field(..., json_schema_extra={"example": "dQw4w9WgXcQ"})
    youtube_url: str = Field(..., json_schema_extra={"example": "https://youtu.be/dQw4w9WgXcQ"})
    title: str = Field(..., json_schema_extra={"example": "My Awesome Video"})
    privacy_status: str = Field(..., json_schema_extra={"example": "private"})
    category_id: str = Field(..., json_schema_extra={"example": "22"})
    tags: List[str] = Field(default_factory=list, json_schema_extra={"example": ["vlog", "tech"]})


class HealthResponse(BaseModel):
    """Response model for service health check."""

    status: str = Field("ok", json_schema_extra={"example": "ok"})
    version: str = Field(__version__, json_schema_extra={"example": "0.1.0"})


@app.get("/health", response_model=HealthResponse, tags=["Health"])
async def health_check():
    """Health check endpoint to verify service availability."""
    return HealthResponse(status="ok", version=__version__)


@app.post(
    "/upload",
    response_model=UploadResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Upload"],
)
async def upload_endpoint(
    file: UploadFile = File(..., description="Video file to upload"),
    title: Optional[str] = Form(None, description="Optional custom video title"),
    description: Optional[str] = Form("", description="Optional video description"),
    privacy_status: Optional[str] = Form(
        None, description="Privacy status: 'private', 'unlisted', or 'public'"
    ),
    category_id: Optional[str] = Form(None, description="YouTube Category ID (e.g. '22')"),
    tags: Optional[str] = Form(None, description="Comma-separated tags (e.g. 'tag1,tag2')"),
    mock: Optional[bool] = Form(None, description="Force mock mode without API credentials"),
):
    """Upload a video file to YouTube."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="Uploaded file must have a valid filename.")

    # Parse tags comma-separated string if provided
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []

    # Temporary directory for handling the stream file safely
    temp_dir = tempfile.mkdtemp(prefix="yt_upload_")
    temp_file_path = os.path.join(temp_dir, file.filename)

    try:
        # Save received file stream to temporary location
        logger.info("Receiving video file: %s", file.filename)
        with open(temp_file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        # Call upload logic
        is_mock = mock if mock is not None else settings.mock_youtube_api

        result = upload_video(
            file_path=temp_file_path,
            title=title,
            description=description,
            privacy_status=privacy_status,
            category_id=category_id,
            tags=tag_list,
            mock_mode=is_mock,
        )

        return UploadResponse(**result)

    except FileNotFoundError as err:
        logger.error("File error: %s", err)
        raise HTTPException(status_code=404, detail=str(err)) from err
    except ValueError as err:
        logger.error("Validation error: %s", err)
        raise HTTPException(status_code=400, detail=str(err)) from err
    except Exception as err:
        logger.error("Upload error: %s", err, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(err)}") from err
    finally:
        # Clean up temp file and directory
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)
