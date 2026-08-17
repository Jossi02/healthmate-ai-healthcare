import asyncio
import os
from pathlib import Path
from typing import Literal, Optional

from dotenv import load_dotenv
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

api_key = os.getenv("GEMINI_API_KEY")
model_name = os.getenv("GEMINI_MODEL_NAME", "gemini-2.5-flash")

if not api_key:
    raise RuntimeError("GEMINI_API_KEY is required to run this external API schema check")

client = genai.Client(api_key=api_key)


class ProfileChange(BaseModel):
    field: str
    value: str


class TestSchema(BaseModel):
    intent: str
    # profile_changes: Optional[dict] = None  # This triggers the error
    profile_changes: Optional[list[ProfileChange]] = None


async def test():
    try:
        response = await client.aio.models.generate_content(
            model=model_name,
            contents="hello",
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=TestSchema,
            ),
        )
        print("Success:", response.text)
    except Exception as exc:
        print("Error:", exc)


if __name__ == "__main__":
    asyncio.run(test())
