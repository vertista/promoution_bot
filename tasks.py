# -*- coding: utf-8 -*-
# Этот "рабочий" работает в отдельном процессе.
# Его задача - брать задачи из очереди и выполнять их.
# Если он упадет, основной бот продолжит работать.

import os
import time
import json
import redis
import aiohttp
# ... (Все импорты для сбора статистики: re, bs4, googleapiclient) ...

from dotenv import load_dotenv

# --- CONFIGURATION ---
load_dotenv()
TOKEN = os.getenv("TOKEN")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")
# ... (YOUTUBE_API_KEY) ...

# --- REDIS CONNECTION ---
# Redis - это наша "очередь задач"
redis_client = redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379"))

# --- API & SCRAPING FUNCTIONS ---
async def get_video_stats(url: str) -> dict:
    # ... (Здесь наша быстрая асинхронная функция get_video_stats) ...

async def send_admin_report(task, stats):
    # ... (Здесь код для отправки красивого отчета админу) ...

async def main_worker_loop():
    print("⚙️  Worker запущен, ожидает задач...")
    while True:
        # Берем задачу из очереди
        task_data = redis_client.blpop("video_tasks")[1]
        task = json.loads(task_data)
        
        print(f"🔥 Новая задача: обработка видео от {task['username']}")
        
        # Собираем статистику
        stats = await get_video_stats(task['video_url'])
        
        # Отправляем отчет
        await send_admin_report(task, stats)
        
        print(f"✅ Задача для {task['username']} выполнена.")

if __name__ == "__main__":
    import asyncio
    asyncio.run(main_worker_loop())
