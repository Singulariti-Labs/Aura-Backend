from fastapi import APIRouter, HTTPException, Depends
from app.DB.pool import get_pool
from app.DB.Queries.user import get_user_by_auth0_id, get_user
from app.DB.Queries.task import get_tasks_by_user, get_task_by_id
from app.DB.Queries.agent_event import get_events_by_task
from app.api.auth_utils import get_current_user
from asyncpg import Pool

rest_router = APIRouter(prefix="/api")

@rest_router.get("/user/profile")
async def fetch_user_profile(current_user: dict = Depends(get_current_user)):
    pool = await get_pool()
    auth0_id = current_user.get("sub")
    user = await get_user_by_auth0_id(pool, auth0_id)
    if not user:
        raise HTTPException(status_code=404, detail="User record not found. Please sync first.")
    return user

@rest_router.get("/tasks")
async def fetch_my_tasks(current_user: dict = Depends(get_current_user)):
    pool = await get_pool()
    auth0_id = current_user.get("sub")
    user = await get_user_by_auth0_id(pool, auth0_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    tasks = await get_tasks_by_user(pool, user["id"])
    return tasks

@rest_router.get("/task/{task_id}")
async def fetch_task(task_id: str, current_user: dict = Depends(get_current_user)):
    pool = await get_pool()
    auth0_id = current_user.get("sub")
    
    # 1. Get task
    task = await get_task_by_id(pool, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    # 2. Verify ownership
    user = await get_user_by_auth0_id(pool, auth0_id)
    if not user or task["user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Not authorized to view this task")
        
    return task

@rest_router.get("/events/{task_id}")
async def fetch_events(task_id: str, current_user: dict = Depends(get_current_user)):
    pool = await get_pool()
    auth0_id = current_user.get("sub")

    # 1. Verify task ownership before returning events
    task = await get_task_by_id(pool, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    user = await get_user_by_auth0_id(pool, auth0_id)
    if not user or task["user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Not authorized to view these events")

    events = await get_events_by_task(pool, task_id)
    return events
