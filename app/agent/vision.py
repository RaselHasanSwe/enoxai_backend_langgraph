import base64
import logging
from openai import AsyncOpenAI
from app.config import get_settings
from app.agent.prompt import _VISION_PROMPT

settings = get_settings()

logger = logging.getLogger(__name__)

_vision_client = AsyncOpenAI(api_key=settings.openai_api_key.get_secret_value())


async def extract_image_description(
    image_base64: str,
    image_media_type: str = "image/jpeg",
) -> str | None:
    """
    Send image to GPT-4o Vision and return a fashion description string.
    Returns None if extraction fails.
    """
    try:
        response = await _vision_client.chat.completions.create(
            model="gpt-4o",
            max_tokens=200,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{image_media_type};base64,{image_base64}",
                                "detail": "low",  # saves tokens, sufficient for fashion
                            },
                        },
                        {
                            "type": "text",
                            "text": _VISION_PROMPT,
                        },
                    ],
                }
            ],
        )

        content = response.choices[0].message.content

        if content is None:
            logger.warning("VISION | no description returned")
            return None

        description = content.strip()
        logger.info("VISION | extracted description: %s", description)
        return description

    except Exception:
        logger.exception("VISION | extraction failed")
        return None