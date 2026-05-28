import os
import asyncio
from google import genai
from google.genai import types
from app.Prompts.audio_input import AUDIO_INPUT_PROMPT

class STTService:
    """
    STTService handles converting audio input into high-quality polished transcripts
    using Google's Gemini models and the google-genai SDK.
    """

    def __init__(self):
        # Retrieve the API key from environment variable
        self.api_key = os.environ.get("GOOGLE_API_KEY")
        if not self.api_key:
            raise ValueError("GOOGLE_API_KEY environment variable is not set.")
        
        # Initialize the Google GenAI client
        self.client = genai.Client(api_key=self.api_key)
        
        # Default model for multimodal audio tasks
        self.default_model = "gemini-3-flash-preview"

    async def transcribe_and_polish(self, audio_bytes: bytes, mime_type: str) -> str:
        """
        Transcribes the provided audio bytes and returns a clean, polished text transcript
        in a single request.
        
        Input:
        - audio_bytes: The raw binary data of the audio file.
        - mime_type: The MIME type of the audio (e.g., 'audio/wav', 'audio/webm', 'audio/mp3').
        
        Returns:
        - A polished transcript string where fillers are removed, grammar is corrected, 
          and formatting/punctuation are professionally added.
        """
        loop = asyncio.get_event_loop()
        polished_transcript = None
        last_error = None
        
        # Try processing with model fallback starting with Gemini 3 Flash
        models_to_try = [
            self.default_model,
            "gemini-3-flash-preview",
            "gemini-2.5-flash",
            "gemini-2.0-flash",
            "gemini-1.5-flash"
        ]
        
        for model in models_to_try:
            try:
                def _call_gemini_transcribe_and_polish():
                    response = self.client.models.generate_content(
                        model=model,
                        contents=[
                            types.Part.from_bytes(
                                data=audio_bytes,
                                mime_type=mime_type
                            ),
                            AUDIO_INPUT_PROMPT
                        ]
                    )
                    return response.text

                polished = await loop.run_in_executor(None, _call_gemini_transcribe_and_polish)
                if polished and polished.strip():
                    polished_transcript = polished.strip()
                    break
            except Exception as e:
                last_error = e
                print(f"[STTService] Multimodal transcription and polishing failed with model {model}: {e}")
                continue

        if not polished_transcript:
            raise RuntimeError(
                f"Failed to transcribe and polish audio. Last error: {str(last_error)}"
            )

        return polished_transcript
