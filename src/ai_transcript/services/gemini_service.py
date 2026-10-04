from __future__ import annotations

from collections.abc import Callable

from ai_transcript.models import ProcessingMode


class GeminiError(RuntimeError):
    pass


_MODE_INSTRUCTIONS = {
    ProcessingMode.CLEAN_TRANSCRIPT: (
        "Produce a polished transcript. Remove filler, false starts, accidental repetition, "
        "and obvious speech-recognition errors. Preserve every substantive claim, example, "
        "qualification, and timestamp where useful. Do not summarize away information."
    ),
    ProcessingMode.SMART_NOTES: (
        "Infer whether this is a lecture, tutorial, business talk, interview, or meeting. "
        "Create the most useful structured notes for that content type. Use only relevant "
        "sections, such as overview, concepts, definitions, examples, steps, decisions, action "
        "items, mistakes, takeaways, or practice tasks. Add a table/checklist/process flow only "
        "when the source truly benefits from it."
    ),
    ProcessingMode.SUMMARY: (
        "Write a concise, accurate summary with the central ideas, important conclusions, and "
        "action items. Omit filler but not critical caveats."
    ),
    ProcessingMode.DETAILED_NOTES: (
        "Write comprehensive, well-organized reference notes. Preserve details, examples, "
        "definitions, procedures, caveats, and key takeaways. Prefer clear headings and bullets."
    ),
    ProcessingMode.CUSTOM: "Follow the user's custom instructions exactly when safe and grounded.",
}


class GeminiNotesService:
    """Grounded transcript transformation with map/reduce handling for long sessions."""

    def __init__(self, api_key: str, model: str = "gemini-2.5-flash") -> None:
        if not api_key.strip():
            raise GeminiError(
                "No Gemini API key is configured. Add it in Settings or set GEMINI_API_KEY."
            )
        try:
            from google import genai

            self._client = genai.Client(api_key=api_key.strip())
        except Exception as exc:
            raise GeminiError(
                "Gemini could not be initialized. Check the installed google-genai package."
            ) from exc
        self.model = model

    def test_connection(self) -> None:
        try:
            response = self._client.models.generate_content(
                model=self.model,
                contents="Reply with exactly: Connection successful",
            )
            if not getattr(response, "text", "").strip():
                raise GeminiError("Gemini returned an empty response.")
        except GeminiError:
            raise
        except Exception as exc:
            raise GeminiError(
                "Gemini connection failed. Check the API key, model name, and internet connection."
            ) from exc

    @staticmethod
    def _chunks(text: str, max_chars: int = 35_000) -> list[str]:
        paragraphs = text.split("\n\n")
        chunks: list[str] = []
        current: list[str] = []
        current_length = 0
        for paragraph in paragraphs:
            # Handle an unusually large paragraph without exceeding the practical request size.
            parts = [
                paragraph[index : index + max_chars]
                for index in range(0, max(1, len(paragraph)), max_chars)
            ] or [""]
            for part in parts:
                addition = len(part) + 2
                if current and current_length + addition > max_chars:
                    chunks.append("\n\n".join(current))
                    current = []
                    current_length = 0
                current.append(part)
                current_length += addition
        if current:
            chunks.append("\n\n".join(current))
        return chunks

    def _request(self, prompt: str) -> str:
        try:
            response = self._client.models.generate_content(
                model=self.model,
                contents=prompt,
            )
            text = getattr(response, "text", "") or ""
            if not text.strip():
                raise GeminiError("Gemini returned an empty response. Try again in a moment.")
            return text.strip()
        except GeminiError:
            raise
        except Exception as exc:
            raise GeminiError(
                "Gemini processing failed. Check your connection, API quota, and key, then retry."
            ) from exc

    def generate(
        self,
        transcript: str,
        mode: ProcessingMode,
        output_language: str,
        custom_instructions: str = "",
        progress: Callable[[int, int], None] | None = None,
    ) -> str:
        if not transcript.strip():
            raise GeminiError("This session has no transcript to process.")
        if mode is ProcessingMode.CUSTOM and not custom_instructions.strip():
            raise GeminiError("Enter custom instructions before using Custom Prompt mode.")

        instruction = _MODE_INSTRUCTIONS[mode]
        if custom_instructions.strip():
            instruction += f"\n\nUser instructions:\n{custom_instructions.strip()}"
        language_rule = (
            "Use the same language as the transcript."
            if output_language.lower() == "same as transcript"
            else f"Write the output in {output_language}."
        )
        common_rules = f"""
{language_rule}
Return clean Markdown only. Stay strictly grounded in the supplied transcript: never invent
facts, quotes, deadlines, examples, or assignments. Preserve technical terms (API, Python,
JavaScript, HTML, CSS, SERP, Search Volume, and similar terms) in English where appropriate.
When speech mixes Urdu/Hindi with English, retain technical English naturally. If source text is
uncertain or contradictory, say so rather than guessing.
""".strip()

        chunks = self._chunks(transcript)
        partials: list[str] = []
        total_steps = len(chunks) + (1 if len(chunks) > 1 else 0)
        for index, chunk in enumerate(chunks, start=1):
            if progress:
                progress(index, total_steps)
            prompt = f"""You are processing part {index} of {len(chunks)} of one transcript.

TASK
{instruction}

RULES
{common_rules}

TRANSCRIPT PART {index}
---
{chunk}
---
"""
            partials.append(self._request(prompt))

        if len(partials) == 1:
            return partials[0]
        if progress:
            progress(total_steps, total_steps)
        combined = "\n\n---\n\n".join(
            f"PART {index}\n{part}" for index, part in enumerate(partials, start=1)
        )
        synthesis_prompt = f"""Combine the processed sections below into one coherent final document.
Deduplicate overlap, retain all important details, and repair heading/numbering continuity.
Do not add information absent from the sections.

ORIGINAL TASK
{instruction}

RULES
{common_rules}

PROCESSED SECTIONS
{combined}
"""
        return self._request(synthesis_prompt)
