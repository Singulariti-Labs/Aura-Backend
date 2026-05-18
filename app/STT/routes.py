from fastapi import APIRouter, UploadFile, File, HTTPException
from app.STT.service import STTService

stt_router = APIRouter(prefix="/audio")

@stt_router.post("/input")
async def audio_input(audio: UploadFile = File(...)):
    """
    API endpoint to accept an audio file, transcribe it using Gemini,
    and return the polished transcript.
    """
    if not audio:
        raise HTTPException(status_code=400, detail="No audio file provided.")
    
    try:
        # Read the uploaded audio bytes
        audio_bytes = await audio.read()
        mime_type = audio.content_type or "audio/wav"
        
        # Process the transcription and polishing via STTService
        stt_service = STTService()
        transcript = await stt_service.transcribe_and_polish(
            audio_bytes=audio_bytes,
            mime_type=mime_type
        )
        
        return {
            "status": "success",
            "transcript": transcript
        }
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Speech-to-text processing failed: {str(e)}"
        )
