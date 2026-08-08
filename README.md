# YouTube Video Uploader FastAPI Microservice

An automated video publishing service built with Python and FastAPI that publishes videos directly to YouTube via YouTube Data API v3.

## Features

- **FastAPI Microservice**: Web API endpoint (`POST /upload`) for automated video ingestion.
- **Resumable Uploads**: Supports chunked, resumable uploads for large video files.
- **OAuth2 Authentication**: Auto-refreshing OAuth token management stored safely in `token.json`.
- **Command-line Interface**: CLI tool (`python -m youtube_uploader.cli`) for quick uploads from terminal.
- **Mock/Dry-Run Mode**: Test upload pipelines locally without Google API credentials or consuming quota.
- **Extensible Architecture**: Ready to integrate with AI metadata generator pipelines (titles, descriptions, thumbnails).

---

## Directory Structure

```text
.
├── youtube_uploader/
│   ├── __init__.py
│   ├── app.py          # FastAPI application & endpoints
│   ├── auth.py         # Google OAuth2 login & token refresh
│   ├── cli.py          # Command-line interface
│   ├── config.py       # Pydantic Settings management
│   └── upload.py       # Core YouTube Data API chunked upload logic
├── tests/
│   ├── test_api.py     # FastAPI endpoint integration tests
│   └── test_upload.py  # Upload module unit tests
├── .env.example
├── .gitignore
├── plan.md
├── README.md
└── requirements.txt
```

---

## Setup Instructions

### 1. Create Virtual Environment & Install Dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure Google Cloud OAuth Credentials (For Live Uploads)

1. Go to [Google Cloud Console](https://console.cloud.google.com/).
2. Create a Project and enable **YouTube Data API v3**.
3. Configure the **OAuth consent screen** (add scope: `https://www.googleapis.com/auth/youtube.upload`).
4. Go to **Credentials** -> **Create Credentials** -> **OAuth 2.0 Client ID**.
5. Select **Desktop Application** as Application Type.
6. Download the JSON file, rename it to `credentials.json`, and place it in the project root directory.

*Note: You can test the entire pipeline in `--mock` mode without `credentials.json`.*

---

## Quickstart Usage

### Running the Streamlit Web UI

Run Streamlit on port `8502` so that port `8501` remains available for the initial Google OAuth browser login callback (`http://localhost:8501/oauth2callback`):

```bash
streamlit run app.py --server.port 8502
```

Once you authenticate with Google for the first time, your access token is saved in `token.json` and future uploads will authenticate automatically.

- **API Documentation (Swagger UI)**: Open [http://localhost:8000/docs](http://localhost:8000/docs)
- **Health Check**: `GET http://localhost:8000/health`

#### Example `curl` Request to Upload Video:

```bash
curl -X POST "http://localhost:8000/upload" \
  -F "file=@my_video.mp4" \
  -F "title=My Automated Upload" \
  -F "description=Uploaded via YouTube Uploader API" \
  -F "privacy_status=private" \
  -F "tags=python,automation,fastapi" \
  -F "mock=true"
```

#### Example Response:

```json
{
  "status": "uploaded",
  "mock": true,
  "video_id": "mock_vid_1770441900",
  "youtube_url": "https://youtu.be/mock_vid_1770441900",
  "title": "My Automated Upload",
  "privacy_status": "private",
  "category_id": "22",
  "tags": ["python", "automation", "fastapi"]
}
```

---

### Option 2: Using the Command Line Interface (CLI)

```bash
# Upload video in mock mode
python -m youtube_uploader.cli my_video.mp4 --title "CLI Upload" --privacy private --mock

# Live upload (requires credentials.json)
python -m youtube_uploader.cli my_video.mp4 --title "Live Upload" --privacy private
```

---

## Running Tests

Run unit tests and API integration tests using `pytest`:

```bash
pytest -v
```

---

## License

MIT License
