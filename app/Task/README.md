# Task execution architecture

Task handling is split into two layers so the current single-process service can
later move to Redis without changing WebSocket or agent code.

## Components

- `TaskCoordinator/`: portable admission state and queue policy.
  - `base.py`: backend-neutral `TaskCoordinator` contract.
  - `memory.py`: atomic single-process implementation.
  - `config.py`: validated environment configuration.
  - `factory.py`: selects the configured backend.
  - `models.py`: lifecycle, admission, and cleanup models.
- `task_manager.py`: local-only runtime objects such as WebSockets,
  `asyncio.Task`, pause events, and client input queues.
- `task_scheduler.py`: application service that joins the coordinator, runtime
  registry, database status updates, queue promotion, and terminal cleanup.

## Invariants

1. A user may run tasks in multiple chats concurrently.
2. A `(user_id, chat_id)` pair may own only one queued or running task.
3. Every control message is authorized against the authenticated `user_id`.
4. Running-task limits apply to execution slots; excess accepted tasks queue.
5. Completed, failed, and cancelled tasks release all coordinator/runtime state.
6. WebSocket messages always include `task_id` and `chat_id`.

## Lifecycle

```text
request -> queued -> running -> completed | failed | cancelled
             |          |
             +----------+-> cancelled
```

Terminal cleanup is idempotent. Releasing a task also scans the FIFO queue and
promotes eligible work without allowing one user at their limit to block other
users.

## Current deployment restriction

`TASK_COORDINATOR_BACKEND=memory` is intentionally single-process. Run one
Uvicorn worker and one application instance. Multiple processes would maintain
independent chat locks and running-task counters.

## Redis migration

Add a `RedisTaskCoordinator` implementing `TaskCoordinator`, then select it in
`factory.py`. The expected Redis mappings are:

- `submit_task`: atomic Lua script using a chat lock, task hash, user-running
  set/counter, and pending stream/list.
- `finish_task`: owner-checked chat-lock release, counter decrement, task
  removal, and atomic promotion.
- `get_task` / `is_owned_by`: task hash lookups.
- `running_count` / `queued_count`: counters maintained by the scripts.

The local runtime registry must remain local because Redis cannot store
WebSockets, coroutine objects, Events, or asyncio Queues. When execution moves
to separate worker processes, replace the local runner factory with a job
publisher and route task events/client input through Redis Streams or another
durable message broker.
