"""
AI Fashion Mockup Generator — takes a fabric photo + a garment style, and
generates a photorealistic product photo of that garment made from the
fabric's pattern. Uses OpenAI's gpt-image-1-mini via the image-edit endpoint
(image-to-image), passing the real fabric photo as the reference so the
pattern/colors in the output stay faithful to the actual fabric.
"""
import base64
import httpx

from openai import AsyncOpenAI

from app.config import settings

client = AsyncOpenAI(api_key=settings.openai_api_key)

GARMENT_STYLES = {
    "shirt": {"label_th": "เสื้อเชิ้ต / เสื้อคลุม", "prompt_en": "a modern fashion shirt or jacket"},
    "dress": {"label_th": "ชุดเดรส", "prompt_en": "an elegant dress"},
    "skirt": {"label_th": "กระโปรง", "prompt_en": "a stylish skirt"},
    "pants": {"label_th": "กางเกง", "prompt_en": "modern trousers"},
    "accessories": {"label_th": "เครื่องประดับ / กระเป๋า", "prompt_en": "a fashion accessory such as a bag or scarf"},
}

_PROMPT_TEMPLATE = (
    "Create a professional, photorealistic fashion e-commerce product photo of "
    "{garment_prompt}, made using {fabric_name} fabric pattern. "
    "Worn by a model or displayed on a clean studio background with professional lighting. "
    "Preserve the authentic Thai textile pattern accurately."
)

async def generate_fashion_image(fabric_image_bytes: bytes, garment_style: str, fabric_name: str) -> bytes:
    style = GARMENT_STYLES.get(garment_style, GARMENT_STYLES["shirt"])
    prompt = _PROMPT_TEMPLATE.format(garment_prompt=style["prompt_en"], fabric_name=fabric_name)

    result = await client.images.generate(
        model="dall-e-3",
        prompt=prompt,
        size="1024x1024",
        response_format="b64_json"
    )
    item = result.data[0]
    if getattr(item, "b64_json", None):
        return base64.b64decode(item.b64_json)
    raise RuntimeError("dall-e-3 returned no image data")
