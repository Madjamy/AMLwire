"""
Generate a cover image for an AML article using Gemini 2.5 Flash via OpenRouter.
Uploads the image to Supabase Storage and returns the public URL.
Returns None if generation or upload fails — pipeline continues without an image.
"""

import os
import base64
import uuid
from pathlib import Path
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
IMAGE_MODEL = "google/gemini-2.5-flash-image"
STORAGE_BUCKET = "article-images"

IMAGE_DIR = Path(".tmp/images")


def _build_image_prompt(title: str, summary: str, region: str, typology: str) -> str:
    region_str = region if region and region != "Not clearly identified" else "global"
    typology_str = (
        typology
        if typology and typology not in ("General AML news", "Sanctions case")
        else "financial investigation"
    )
    return (
        f"Professional editorial illustration for a financial crime news article. "
        f"Topic: {typology_str}. Region: {region_str}. "
        f"Visual style: dark, corporate, investigative journalism aesthetic. "
        f"Elements may include: currency, documents, legal scales, digital networks, "
        f"surveillance, or law enforcement motifs. "
        f"No readable text, no identifiable human faces. "
        f"Wide format, high quality, modern digital art suitable for a news website header."
    )


def _decode_base64_image(b64_data: str) -> bytes:
    """Decode base64 image data to bytes."""
    return base64.b64decode(b64_data)


def _upload_to_supabase(image_bytes: bytes, filename: str) -> str | None:
    """Upload image bytes to Supabase Storage. Returns public URL or None."""
    try:
        from supabase import create_client
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_SERVICE_KEY")
        if not url or not key:
            return None
        client = create_client(url, key)
        client.storage.from_(STORAGE_BUCKET).upload(
            path=filename,
            file=image_bytes,
            file_options={"content-type": "image/png", "upsert": "true"},
        )
        public_url = f"{url}/storage/v1/object/public/{STORAGE_BUCKET}/{filename}"
        return public_url
    except Exception as e:
        print(f"[ImageGen] Storage upload failed: {e}")
        return None


def generate_image(title: str, summary: str, region: str = "", typology: str = "") -> str | None:
    """
    Generate a cover image using Gemini 2.5 Flash via OpenRouter,
    upload it to Supabase Storage, and return the public URL.
    Returns None on any failure — pipeline continues with image_url = null.
    """
    if not OPENROUTER_API_KEY:
        print("[ImageGen] OPENROUTER_API_KEY not set — skipping image generation")
        return None

    client = OpenAI(
        api_key=OPENROUTER_API_KEY,
        base_url=OPENROUTER_BASE_URL,
    )

    prompt = _build_image_prompt(title, summary, region, typology)

    try:
        response = client.chat.completions.create(
            model=IMAGE_MODEL,
            messages=[{"role": "user", "content": prompt}],
            extra_body={"modalities": ["image"]},
        )

        message = response.choices[0].message
        image_bytes = None

        # OpenRouter Gemini returns images in message.images (list of dicts)
        images = getattr(message, "images", None)
        if images:
            for img in images:
                url = img.get("image_url", {}).get("url", "")
                if url.startswith("data:image"):
                    image_bytes = _decode_base64_image(url.split(",", 1)[1])
                    break
                elif url.startswith("http"):
                    print(f"[ImageGen] URL: {url}")
                    return url

        # Fallback: check message.content
        if not image_bytes:
            content = message.content
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "image_url":
                        data = part.get("image_url", {}).get("url", "")
                        if data.startswith("data:image"):
                            image_bytes = _decode_base64_image(data.split(",", 1)[1])
                            break
                        elif data.startswith("http"):
                            return data
            elif isinstance(content, str) and content.startswith("data:image"):
                image_bytes = _decode_base64_image(content.split(",", 1)[1])

        if not image_bytes:
            print("[ImageGen] Unexpected response — no image data found")
            return None

        filename = f"{uuid.uuid4().hex}.png"
        public_url = _upload_to_supabase(image_bytes, filename)
        if public_url:
            print(f"[ImageGen] Uploaded: {public_url}")
            return public_url

        # Fallback: save locally if storage upload failed
        IMAGE_DIR.mkdir(parents=True, exist_ok=True)
        local_path = IMAGE_DIR / filename
        with open(local_path, "wb") as f:
            f.write(image_bytes)
        print(f"[ImageGen] Saved locally (storage failed): {local_path}")
        return None

    except Exception as e:
        print(f"[ImageGen] Error for '{title[:50]}': {e}")
        return None


if __name__ == "__main__":
    result = generate_image(
        title="AUSTRAC fines Westpac $1.3 billion for AML failures",
        summary="Australia fined Westpac for failing to report suspicious transactions.",
        region="Australia",
        typology="AML control failures",
    )
    print(f"Result: {result}")
