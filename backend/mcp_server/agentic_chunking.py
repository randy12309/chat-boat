"""Agentic document chunking using Google Gemini for semantic boundary detection."""
import os
import json
import re
from typing import List
from pydantic import BaseModel, Field

# Import google-genai
try:
    from google import genai
    from google.genai import types
    GENAI_AVAILABLE = True
except ImportError:
    GENAI_AVAILABLE = False

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "models/gemini-3.1-flash-lite")

# Maximum characters per chunk for embedding
CHUNK_MAX_CHARS = 1500
CHUNK_OVERLAP = 200


class ChunkPlan(BaseModel):
    """Represents the chunking plan returned by the agent."""
    chunks: List[str] = Field(description="List of chunk texts")


def _build_chunking_prompt(text: str, max_chars: int = CHUNK_MAX_CHARS) -> str:
    """Build the prompt for agentic chunking."""
    return f"""You are an expert document processor. Your task is to split the following document text into semantically coherent chunks for vector embedding.

RULES:
1. Each chunk should be a complete, self-contained semantic unit (a topic, section, or logical group of paragraphs)
2. Target chunk size: ~{max_chars} characters (range: {max_chars//2} to {max_chars*2})
3. Preserve complete sentences and paragraphs - never cut mid-sentence
4. Group related content together (e.g., a policy definition with its examples, a FAQ with its answer)
5. Keep headers/titles with their content
6. Return ONLY a JSON object with a "chunks" array of strings

DOCUMENT TEXT:
{text[:50000]}  # Limit input to avoid token limits

Return format:
{{
  "chunks": [
    "First chunk text...",
    "Second chunk text...",
    ...
  ]
}}"""


def agentic_chunk(text: str, max_chars: int = CHUNK_MAX_CHARS) -> List[str]:
    """
    Use Gemini to intelligently chunk a document into semantic units.

    Falls back to simple chunking if Gemini is unavailable or fails.
    """
    if not GENAI_AVAILABLE:
        print("google-genai not available, falling back to simple chunking")
        return simple_chunk(text, max_chars)

    if not GEMINI_API_KEY:
        print("GEMINI_API_KEY not set, falling back to simple chunking")
        return simple_chunk(text, max_chars)

    try:
        client = genai.Client(api_key=GEMINI_API_KEY)

        prompt = _build_chunking_prompt(text, max_chars)

        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.1,
                response_mime_type="application/json",
            ),
        )

        if response.text:
            result = json.loads(response.text)
            chunks = result.get("chunks", [])

            # Validate and clean chunks
            cleaned_chunks = []
            for chunk in chunks:
                chunk = chunk.strip()
                if chunk and len(chunk) > 50:  # Minimum meaningful chunk size
                    # Ensure chunk doesn't exceed max significantly
                    if len(chunk) > max_chars * 2:
                        # Split oversized chunks
                        cleaned_chunks.extend(simple_chunk(chunk, max_chars))
                    else:
                        cleaned_chunks.append(chunk)

            if cleaned_chunks:
                return cleaned_chunks

    except Exception as e:
        print(f"Agentic chunking failed: {e}, falling back to simple chunking")

    return simple_chunk(text, max_chars)


def simple_chunk(text: str, size: int = 1000, overlap: int = 150) -> List[str]:
    """Fallback: Pack paragraphs into chunks of about `size` characters."""
    import re
    units: list[str] = []
    for paragraph in re.split(r"\n\s*\n", text):
        paragraph = re.sub(r"\s+", " ", paragraph).strip()
        while len(paragraph) > size:
            cut = paragraph.rfind(" ", 0, size)
            cut = cut if cut > size // 2 else size
            units.append(paragraph[:cut])
            paragraph = paragraph[cut:].lstrip()
        if paragraph:
            units.append(paragraph)
    chunks: list[str] = []
    current = ""
    for unit in units:
        if current and len(current) + 1 + len(unit) > size:
            chunks.append(current)
            tail = current[-overlap:]
            current = tail[tail.find(" ") + 1:] if " " in tail else tail
        current = f"{current} {unit}".strip()
    if current:
        chunks.append(current)
    return chunks


def chunk_for_embedding(text: str, use_agentic: bool = True) -> List[str]:
    """
    Main entry point for document chunking.

    Args:
        text: Full document text
        use_agentic: Whether to use agentic chunking (default True)

    Returns:
        List of chunk strings ready for embedding
    """
    if use_agentic:
        return agentic_chunk(text)
    return simple_chunk(text)


# For testing
if __name__ == "__main__":
    test_text = """
    SAG-AFTRA Health Plan Policy Reference

    P01 Eligibility Requirements
    Record type: Policy | Sources: S01
    Effective scope: All participants

    To be eligible for the SAG-AFTRA Health Plan, a participant must have earned
    covered earnings of at least $28,000 in the preceding four quarters. This threshold
    is updated annually by the Board of Trustees.

    P02 Premium Payments
    Record type: Policy | Sources: S02
    Effective scope: All eligible participants

    Quarterly premiums are due on the first day of each quarter. The standard premium
    is $700 per quarter. Participants may pay via credit card, ACH, or payroll deduction.
    Late payments incur a 1.5% monthly interest charge.

    Q001 What is the eligibility threshold?
    Mode: REFERENCE | Policies: P01 | Sources: S01
    The eligibility threshold is $28,000 in covered earnings over the preceding four quarters.

    Q002 How do I pay my premium?
    Mode: LIVE | Policies: P02 | Sources: S02
    You can pay your premium online via the member portal using credit card or ACH.
    """

    chunks = agentic_chunk(test_text)
    for i, chunk in enumerate(chunks):
        print(f"--- Chunk {i+1} ({len(chunk)} chars) ---")
        print(chunk)
        print()