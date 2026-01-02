from fastapi import APIRouter, HTTPException, Depends
from app.DB.pool import get_pool
from app.DB.Queries.user import get_user
from app.DB.Queries.task import get_tasks_by_user, get_task_by_id
from app.DB.Queries.agent_event import get_events_by_task
from asyncpg import Pool

rest_router = APIRouter(prefix="/api")

@rest_router.get("/user/{user_id}")
async def fetch_user(user_id: str):
    pool = await get_pool()
    user = await get_user(pool, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user

@rest_router.get("/tasks/{user_id}")
async def fetch_tasks(user_id: str):
    pool = await get_pool()
    tasks = await get_tasks_by_user(pool, user_id)
    return tasks

@rest_router.get("/task/{task_id}")
async def fetch_task(task_id: str):
    pool = await get_pool()
    task = await get_task_by_id(pool, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task

@rest_router.get("/events/{task_id}")
async def fetch_events(task_id: str):
    pool = await get_pool()
    events = await get_events_by_task(pool, task_id)
    return events
