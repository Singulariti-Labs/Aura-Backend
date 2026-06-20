from app.STT.realtime_types import TranscriptResult


class TranscriptAggregator:
    """Collects final Deepgram fragments into complete utterances."""

    def __init__(self) -> None:
        self._final_parts: list[str] = []

    def add_result(self, result: TranscriptResult) -> str | None:
        """
        Add a normalized Deepgram result.

        Returns a completed utterance only when Deepgram indicates speech has
        ended. Interim text is intentionally not aggregated for polishing.
        """

        text = result.text.strip()
        if result.is_final and text:
            self._final_parts.append(text)

        if result.speech_final:
            return self.flush()

        return None

    def flush(self) -> str | None:
        """Return and clear any accumulated final transcript text."""

        if not self._final_parts:
            return None

        utterance = " ".join(self._final_parts).strip()
        self._final_parts.clear()
        return utterance or None

    def has_pending(self) -> bool:
        """Return whether final fragments are waiting to be emitted."""

        return bool(self._final_parts)
