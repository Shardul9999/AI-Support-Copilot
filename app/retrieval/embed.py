import os

from google import genai
from google.genai import types

_client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

MODEL = "models/gemini-embedding-001"
DIMENSIONS = 768

def _embed(text: str, task_type: str) -> list[float]:
    result = _client.models.embed_content(
        model=MODEL,
        contents=text,
        config=types.EmbedContentConfig(task_type=task_type, output_dimensionality=DIMENSIONS),
    )
    return result.embeddings[0].values

def embed_document(text: str) -> list[float]:
    """Embed a knowledge-base chunk, for storage."""
    return _embed(text, "RETRIEVAL_DOCUMENT")

def embed_query(text: str) -> list[float]:
    """Embed an incoming ticket, for search. Gemini tunes the embedding
    differently depending on task_type, so queries and documents use
    separate functions even though both just call embed_content."""
    return _embed(text, "RETRIEVAL_QUERY")
