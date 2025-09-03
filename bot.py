# -*- coding: utf-8 -*-
# Этот файл теперь отвечает ТОЛЬКО за общение с пользователем.
# Он работает мгновенно и не занимается сбором данных.

import os
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)
from tasks import add_task_to_queue

# --- CONFIGURATION ---
load_dotenv()
TOKEN = os.getenv("TOKEN")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")

# --- DATABASE & BOT STATES (Остаются для сбора реквизитов) ---
# ... (Весь код для базы данных и диалогов остается здесь, как и раньше) ...
# Я его скрыл для краткости, но он должен быть здесь.

# --- HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # ... (Код для /start) ...

async def handle_submission(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message_text = update.message.text
    if not ("tiktok.com" in message_text or "youtube.com" in message_text or "youtu.be" in message_text):
        await update.message.reply_text("❌ Принимаю только ссылки TikTok и YouTube.")
        return

    # 1. Мгновенно отвечаем пользователю
    await update.message.reply_text("✅ Спасибо! Ваша заявка принята в обработку.")

    # 2. Добавляем задачу в очередь для "рабочего"
    task = {
        "user_id": update.effective_user.id,
        "username": update.effective_user.username or update.effective_user.first_name,
        "video_url": message_text
    }
    add_task_to_queue(task)
    
# ... (Остальные обработчики: setup_payment, button_handler и т.д.) ...

def main() -> None:
    if not all([TOKEN, ADMIN_CHAT_ID]):
        print("❌ Ошибка: отсутствуют TOKEN или ADMIN_CHAT_ID.")
        return

    application = Application.builder().token(TOKEN).build()
    
    # ... (Регистрация всех обработчиков) ...
    
    print("🤖 Основной бот запущен...")
    application.run_polling()

if __name__ == "__main__":
    main()

