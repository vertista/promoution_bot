import os
import json
import redis
from dotenv import load_dotenv

load_dotenv()
redis_client = redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379"))

def add_task_to_queue(task: dict):
    """Добавляет задачу в очередь Redis."""
    redis_client.rpush("video_tasks", json.dumps(task))
