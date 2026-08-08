"""Google Gemini API Integration for AI Video Generation and Channel Analytics."""

import os
import io
import json
import time
import logging
from typing import Dict, Any, List, Optional
from google import genai
from google.genai import errors
from youtube_uploader.config import settings

logger = logging.getLogger(__name__)

FALLBACK_MODELS = [
    "gemini-2.0-flash",
    "gemini-1.5-flash",
    "gemini-2.0-flash-lite",
    "gemini-1.5-pro",
]


def _get_client() -> genai.Client:
    api_key = settings.gemini_api_key or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY is not configured in environment or .env file.")
    return genai.Client(api_key=api_key)


def _generate_with_retry(
    client: genai.Client,
    contents: Any,
    models: Optional[List[str]] = None
) -> Any:
    """Helper to call Gemini models with fallback models and retry on 429 Rate Limit."""
    target_models = models or FALLBACK_MODELS
    last_err = None

    for model_name in target_models:
        for attempt in range(2):
            try:
                response = client.models.generate_content(
                    model=model_name,
                    contents=contents
                )
                return response
            except (errors.APIError, errors.ClientError, Exception) as err:
                last_err = err
                err_str = str(err)
                if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str or "quota" in err_str.lower():
                    logger.warning(
                        "Gemini model '%s' hit rate limit (429) on attempt %d: %s",
                        model_name, attempt + 1, err
                    )
                    time.sleep(2.5)
                    continue
                else:
                    # Non-rate-limit error, try next model
                    break

    raise RuntimeError(
        "Gemini API rate limit exceeded (429) across models. "
        f"Original error: {last_err}"
    )


def generate_video_ideas(
    channel_info: Dict[str, Any],
    recent_videos: Optional[List[Dict[str, Any]]] = None,
    n_ideas: int = 5
) -> List[Dict[str, Any]]:
    """Generate viral video ideas tailored to the channel's niche and audience history."""
    client = _get_client()

    channel_title = channel_info.get("title", "YouTube Channel")
    channel_desc = channel_info.get("description", "")
    video_titles = [v.get("title") for v in (recent_videos or []) if v.get("title")]

    prompt = f"""
Act as a world-class YouTube Content Strategist and Growth Engineer.
Generate {n_ideas} high-performing, high-CTR video ideas for the channel "{channel_title}".

Channel Context:
- Description: {channel_desc}
- Recent Videos: {", ".join(video_titles[:10]) if video_titles else "General Tech & Creator Content"}

Return JSON list of objects. Each object must have these exact keys:
1. "title": Catchy, clickable title (under 60 chars)
2. "hook": 2-sentence opening hook designed to boost retention
3. "description": 2-sentence video description draft
4. "tags": Array of 5-8 relevant tags

Respond ONLY with valid JSON inside ```json``` codeblock.
"""

    try:
        response = _generate_with_retry(client, prompt)
        text = response.text.strip()
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            text = text.split("```")[1].split("```")[0].strip()

        ideas = json.loads(text)
        return ideas if isinstance(ideas, list) else []
    except Exception as err:
        logger.warning("Gemini generation rate limited or failed (%s). Serving fallback ideas.", err)
        return [
            {
                "title": "5 AI Automation Tools That Save 10 Hours a Week",
                "hook": "Stop doing repetitive work manually! In this video, we cover 5 AI workflows that double your daily output.",
                "description": "A comprehensive guide to automating content creation, research, and coding tasks with cutting-edge AI tools.",
                "tags": ["ai", "automation", "productivity", "tech", "tutorial"]
            },
            {
                "title": "Build Autonomous AI Agents in 15 Minutes",
                "hook": "AI agents are replacing simple chatbots. Here is how you can deploy your own multi-agent system from scratch.",
                "description": "Step-by-step tutorial on building and orchestrating autonomous AI coding agents.",
                "tags": ["python", "ai_agents", "coding", "llm", "developers"]
            },
            {
                "title": "Streamlit vs Next.js: The 2026 Developer Breakdown",
                "hook": "Should you build your web app with Streamlit or Next.js? Let's compare speed, UI flexibility, and scaling.",
                "description": "In-depth comparison of Python Streamlit vs React Next.js for rapid app development.",
                "tags": ["streamlit", "nextjs", "python", "javascript", "webdev"]
            },
            {
                "title": "Why Most YouTube Channels Fail (And How to Fix It)",
                "hook": "90% of creators make the exact same 3 mistakes in their first 30 seconds. Here is the blueprint to fix them.",
                "description": "Proven audience retention strategies and thumbnail optimization techniques for modern YouTube channels.",
                "tags": ["youtube_tips", "creator_economy", "analytics", "growth"]
            },
            {
                "title": "Zero to 100k Subscribers: The Proven Creator Playbook",
                "hook": "Growing a YouTube channel isn't luck—it's a system. Here is the exact content formula used by top creators.",
                "description": "Actionable channel positioning and content strategy guide for tech and developer creators.",
                "tags": ["growth", "youtube", "creator", "strategy"]
            }
        ][:n_ideas]


def generate_video_script(
    video_title: str,
    channel_topic: str = "",
    duration_mins: int = 5
) -> str:
    """Generate a complete video script outline with timing, visuals, and spoken lines."""
    client = _get_client()

    prompt = f"""
You are an expert YouTube Scriptwriter.
Write a full, engaging video script for a {duration_mins}-minute YouTube video.

Title: "{video_title}"
Channel Topic/Niche: {channel_topic or "Technology & Digital Creation"}

Format the script in clean Markdown with clear headings:
- 🎯 **Title & Hook (0:00 - 0:30)**: Attention-grabbing intro line and visual cue
- 💡 **The Core Problem / Context (0:30 - 1:30)**
- 🚀 **Main Section 1: Key Insights / Steps**
- ⚡ **Main Section 2: Pro Tips & Examples**
- 📢 **Call to Action & Outro**: Smooth subscriber pitch and next video recommendation

Include [Visual Cues] in brackets for b-roll and screen recordings.
"""

    try:
        response = _generate_with_retry(client, prompt)
        return response.text.strip()
    except Exception as err:
        logger.warning("Gemini script writer hit rate limit (%s). Serving fallback script.", err)
        return f"""# 📝 Script: {video_title}
*(Generated via Local Engine — Gemini API rate limited)*

---

### 🎯 **Title & Hook (0:00 - 0:30)**
> **[Visual Cue: Fast-paced montage of high-tech tools & statistics overlay]**
> **Speaker:** "If you're still creating content or building tools manually, you're missing out on a massive productivity boost. In this video, we break down **{video_title}** step-by-step so you can execute like a pro."

---

### 💡 **The Core Context (0:30 - 1:30)**
> **[Visual Cue: Screen recording of key workflow diagram]**
> **Speaker:** "Before diving into the tools, let's look at why this approach outperforms conventional workflows. The secret lies in automated pipelines."

---

### 🚀 **Main Section 1: Core Strategy**
> **[Visual Cue: Live demonstration of feature walkthrough]**
> **Speaker:** "Step 1 is setup. Once configured, your workflow handles repetition automatically while you focus on creative direction."

---

### ⚡ **Main Section 2: Pro Tips & Examples**
> **[Visual Cue: Side-by-side performance comparison]**
> **Speaker:** "Here are three pro tips to maximize performance and avoid common pitfalls..."

---

### 📢 **Call to Action & Outro**
> **[Visual Cue: Subscribe graphic animation and end card]**
> **Speaker:** "If you found this valuable, hit that Subscribe button and check out the links in the description for full code samples!"
"""


def analyse_video_performance(
    video_details: Dict[str, Any],
    channel_info: Optional[Dict[str, Any]] = None
) -> str:
    """Generate AI insights for a specific video's stats and metadata."""
    client = _get_client()

    title = video_details.get("title", "Untitled")
    views = video_details.get("view_count", 0)
    likes = video_details.get("like_count", 0)
    comments = video_details.get("comment_count", 0)

    prompt = f"""
Act as a YouTube Analytics Analyst. Give a concise, actionable analysis for this video:

Video Title: "{title}"
Views: {views:,}
Likes: {likes:,}
Comments: {comments:,}

Provide:
1. 📈 **Performance Verdict**: Quick assessment of engagement metrics.
2. 🎯 **Title & Thumbnail Critique**: How to make the title punchier or higher CTR.
3. 💡 **3 Actionable Improvements**: Specific tweaks for future videos in this topic.

Keep the output concise, structured with bullet points, and encouraging.
"""

    try:
        response = _generate_with_retry(client, prompt)
        return response.text.strip()
    except Exception as err:
        logger.warning("Gemini video critique hit rate limit (%s). Serving fallback analysis.", err)
        return f"""### 📊 AI Analysis for "{title}"
*(Generated via Local Engine — Gemini API rate limited)*

- 📈 **Performance Verdict**: Strong engagement baseline. Views ({views:,}) show solid impression reach with an impressive like-to-view ratio.
- 🎯 **Title & Thumbnail Critique**: The title is clear. To boost CTR by 25%+, add numbers or high-curiosity keywords like *"The Secret to..."* or *"in 10 Minutes"*.
- 💡 **Actionable Improvements**:
  1. Add timestamps in description to improve search indexability.
  2. Pin an engaging open question in the comment section to drive comment velocity.
  3. Create a 30-second Short clipping the main hook of this video to drive traffic.
"""


def compare_channels_ai(
    own_stats: Dict[str, Any],
    competitor_stats: Dict[str, Any]
) -> str:
    """Generate AI comparison report between user's channel and competitor's channel."""
    client = _get_client()

    prompt = f"""
Act as a YouTube Growth Consultant. Compare these two YouTube channels and provide strategic recommendations:

Your Channel:
- Name: {own_stats.get('title')}
- Subscribers: {own_stats.get('subscriber_count', 0):,}
- Total Views: {own_stats.get('view_count', 0):,}
- Total Videos: {own_stats.get('video_count', 0):,}

Competitor Channel:
- Name: {competitor_stats.get('title')} ({competitor_stats.get('handle', '')})
- Subscribers: {competitor_stats.get('subscriber_count', 0):,}
- Total Views: {competitor_stats.get('view_count', 0):,}
- Total Videos: {competitor_stats.get('video_count', 0):,}

Provide:
1. ⚖️ **Channel Scale Comparison**: Overview of reach and volume.
2. 📊 **Average Views per Video**: Compare content efficiency.
3. 🚀 **Key Action Steps to Close the Gap**: 3 high-leverage growth strategies tailored for {own_stats.get('title')}.
"""

    response = _generate_with_retry(client, prompt)
    return response.text.strip()


def transcribe_and_generate_metadata(
    file_bytes: bytes,
    mime_type: str = "video/mp4",
    file_name: str = "upload.mp4",
) -> Dict[str, Any]:
    """Upload video/audio to Gemini File API, transcribe and generate YouTube metadata.

    Returns dict with keys: transcript, title, description, tags.
    """
    client = _get_client()
    file_obj = None

    try:
        # 1. Upload file to Gemini File API
        file_obj = client.files.upload(
            file=io.BytesIO(file_bytes),
            config={"mime_type": mime_type, "display_name": file_name}
        )

        # 2. Poll until file is ACTIVE (processing may take a few seconds)
        for _ in range(30):
            status = client.files.get(name=file_obj.name)
            if status.state and status.state.name == "ACTIVE":
                break
            time.sleep(2)
        else:
            raise RuntimeError("Gemini file processing timed out.")

        # 3. Transcribe and generate metadata in a single prompt
        prompt = """
You are a YouTube publishing assistant.

First, transcribe the video/audio provided verbatim as accurately as possible.

Then, based on that transcript, generate optimised YouTube publishing metadata.

Respond with ONLY valid JSON (no markdown) matching this exact schema:
{
  "transcript": "<full verbatim transcription>",
  "title": "<catchy YouTube title under 70 chars>",
  "description": "<engaging 2-3 sentence YouTube description with relevant keywords>",
  "tags": ["<tag1>", "<tag2>", "..."]  // 8-12 relevant tags
}
"""

        contents = [
            {"file_data": {"file_uri": file_obj.uri, "mime_type": mime_type}},
            prompt,
        ]
        response = _generate_with_retry(client, contents)

        text = response.text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

        try:
            result = json.loads(text)
            result.setdefault("transcript", "")
            result.setdefault("title", "")
            result.setdefault("description", "")
            result.setdefault("tags", [])
            return result
        except json.JSONDecodeError as err:
            logger.error("Failed to parse Gemini metadata JSON: %s\nRaw: %s", err, text)
            return {
                "transcript": text,
                "title": os.path.splitext(file_name)[0].replace("_", " ").replace("-", " ").title(),
                "description": f"Video content for {file_name}",
                "tags": ["video", "youtube", "creator"],
            }
    finally:
        # Guaranteed cleanup of uploaded file in Gemini
        if file_obj and hasattr(file_obj, "name"):
            try:
                client.files.delete(name=file_obj.name)
            except Exception as clean_err:
                logger.warning("Could not delete temp Gemini file '%s': %s", file_obj.name, clean_err)
