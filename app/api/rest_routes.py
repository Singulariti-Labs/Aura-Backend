from fastapi import APIRouter, HTTPException, Depends, UploadFile, File, Request
from app.DB.pool import get_pool
from app.DB.Queries.user import get_user_by_auth0_id, get_user
from app.DB.Queries.task import get_tasks_by_user, get_task_by_id, delete_task_db, star_task_db, unstar_task_db
from app.DB.Queries.agent_event import get_events_by_task
from app.api.auth_utils import get_current_user
from app.DB.Queries.user_settings import upsert_user_settings, get_user_settings
from app.RateLimit.usage_service import fetch_user_usage
from asyncpg import Pool
from app.STT.service import STTService
from app.GraphMemory.routes import handle_memory_consolidation_request
from app.GraphMemory.schemas import (
    ConsolidationApiRequest,
    ConsolidationApiResponse,
    ConsolidationErrorResponse,
)

from app.Prompts.Templates.aura_template import AURA_TEMPLATE
from app.Prompts.Templates.id_template import ID_TEMPLATE
from app.Prompts.Templates.soul_template import SOUL_TEMPLATE
from app.Prompts.Templates.user_template import USER_TEMPLATE

rest_router = APIRouter(prefix="/api")
memory_api_router = APIRouter(prefix="/v1/memory", tags=["memory"])


@memory_api_router.post(
    "/consolidate",
    response_model=ConsolidationApiResponse,
    responses={
        400: {"model": ConsolidationErrorResponse},
        401: {"model": ConsolidationErrorResponse},
        403: {"model": ConsolidationErrorResponse},
        413: {"model": ConsolidationErrorResponse},
        429: {"model": ConsolidationErrorResponse},
        500: {"model": ConsolidationErrorResponse},
        502: {"model": ConsolidationErrorResponse},
        503: {"model": ConsolidationErrorResponse},
        504: {"model": ConsolidationErrorResponse},
    },
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": ConsolidationApiRequest.model_json_schema()
                }
            },
        }
    },
)
async def consolidate_graph_memory(request: Request):
    """Delegate the authenticated consolidation request to GraphMemory."""

    return await handle_memory_consolidation_request(request)

@rest_router.get("/user/profile")
async def fetch_user_profile(current_user: dict = Depends(get_current_user)):
    pool = await get_pool()
    auth0_id = current_user.get("sub")
    user = await get_user_by_auth0_id(pool, auth0_id)
    if not user:
        raise HTTPException(status_code=404, detail="User record not found. Please sync first.")
    return user

@rest_router.get("/user/usage")
async def fetch_my_usage(current_user: dict = Depends(get_current_user)):
    pool = await get_pool()
    auth0_id = current_user.get("sub")
    user = await get_user_by_auth0_id(pool, auth0_id)
    if not user:
        raise HTTPException(
            status_code=404,
            detail="User record not found. Please sync first.",
        )

    return await fetch_user_usage(pool, user["id"])

@rest_router.get("/get_tasks")
async def fetch_my_tasks(current_user: dict = Depends(get_current_user)):
    pool = await get_pool()
    auth0_id = current_user.get("sub")
    user = await get_user_by_auth0_id(pool, auth0_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    tasks = await get_tasks_by_user(pool, user["id"])
    return tasks

@rest_router.get("/task/{task_id}")
async def fetch_task_by_task_id(task_id: str, current_user: dict = Depends(get_current_user)):
    pool = await get_pool()
    auth0_id = current_user.get("sub")
    
    # 1. Get task
    task = await get_task_by_id(pool=pool, id=task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    # 2. Verify ownership
    user = await get_user_by_auth0_id(pool=pool, auth0_id=auth0_id)
    if not user or task["user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Not authorized to view this task")
        
    return task
    
@rest_router.post("/delete_task/{task_id}")
async def delete_task(task_id: str, current_user: dict = Depends(get_current_user)):
    pool = await get_pool()
    auth0_id = current_user.get("sub")
    
    # 1. Verify ownership (simple approach as requested)
    task = await get_task_by_id(pool=pool, id=task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    user = await get_user_by_auth0_id(pool=pool, auth0_id=auth0_id)
    if not user or task["user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Not authorized to delete this task")
    
    success = await delete_task_db(pool=pool, id=task_id)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to delete task")
        
    return {"status": "success", "message": "Task deleted successfully"}

@rest_router.post("/starred_task/{task_id}")
async def star_task(task_id: str, current_user: dict = Depends(get_current_user)):
    pool = await get_pool()
    auth0_id = current_user.get("sub")
    
    task = await get_task_by_id(pool=pool, id=task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    user = await get_user_by_auth0_id(pool=pool, auth0_id=auth0_id)
    if not user or task["user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Not authorized to star this task")
    
    success = await star_task_db(pool=pool, id=task_id)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to star task")
        
    return {"status": "success", "message": "Task starred successfully"}

@rest_router.post("/unstarred_task/{task_id}")
async def unstar_task(task_id: str, current_user: dict = Depends(get_current_user)):
    pool = await get_pool()
    auth0_id = current_user.get("sub")
    
    task = await get_task_by_id(pool=pool, id=task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    user = await get_user_by_auth0_id(pool=pool, auth0_id=auth0_id)
    if not user or task["user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Not authorized to unstar this task")
    
    success = await unstar_task_db(pool=pool, id=task_id)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to unstar task")
        
    return {"status": "success", "message": "Task unstarred successfully"}

@rest_router.get("/events/{task_id}")
async def fetch_events(task_id: str, current_user: dict = Depends(get_current_user)):
    pool = await get_pool()
    auth0_id = current_user.get("sub")

    # 1. Verify task ownership before returning events
    task = await get_task_by_id(pool=pool, id=task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    user = await get_user_by_auth0_id(pool=pool, auth0_id=auth0_id)
    if not user or task["user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Not authorized to view these events")

    events = await get_events_by_task(pool=pool, task_id=task_id)
    return events

@rest_router.post("/settings")
async def save_settings(settings: dict, current_user: dict = Depends(get_current_user)):
    pool = await get_pool()
    auth0_id = current_user.get("sub")
    
    user = await get_user_by_auth0_id(pool, auth0_id)
    if not user:
        raise HTTPException(status_code=404, detail="User record not found. Please sync first.")
    
    success = await upsert_user_settings(pool, user["id"], settings)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to save settings")
        
    return {"status": "success", "message": "Settings saved successfully"}

@rest_router.get("/settings")
async def fetch_settings(current_user: dict = Depends(get_current_user)):
    pool = await get_pool()
    auth0_id = current_user.get("sub")
    
    user = await get_user_by_auth0_id(pool, auth0_id)
    if not user:
        raise HTTPException(status_code=404, detail="User record not found")
        
    settings = await get_user_settings(pool, user["id"])
    return settings or {}

@rest_router.get("/load-conscious")
async def load_conscious():
    return {
        "aura": AURA_TEMPLATE,
        "id": ID_TEMPLATE,
        "soul": SOUL_TEMPLATE,
        "user": USER_TEMPLATE
    }

@rest_router.post("/audio/input")
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

@rest_router.post("/audio/transcribe")
async def audio_transcribe(audio: UploadFile = File(...)):
    # Accepts an audio file, transcribes it, and converts it into polished dictation based on user intent.
    if not audio:
        raise HTTPException(status_code=400, detail="No audio file provided.")
    
    try:
        # Read the uploaded audio bytes
        audio_bytes = await audio.read()
        mime_type = audio.content_type or "audio/wav"
        
        # Process the transcription via the intent-aware STTService method
        stt_service = STTService()
        transcript = await stt_service.stt_transcription(
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
            detail=f"Audio transcription processing failed: {str(e)}"
        )

