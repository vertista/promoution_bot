# -*- coding: utf-8 -*-
import os
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

# --- CONFIGURATION ---
load_dotenv()

TOKEN = os.getenv("TOKEN")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")

# --- BOT HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Отвечает на команду /start."""
    await update.message.reply_text("Simple bot is running!")

async def forward_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Пересылает любое сообщение админу."""
    if update.message:
        await context.bot.forward_message(
            chat_id=ADMIN_CHAT_ID,
            from_chat_id=update.message.chat_id,
            message_id=update.message.message_id
        )

# --- MAIN FUNCTION ---
def main() -> None:
    """Основная функция для запуска бота."""
    if not all([TOKEN, ADMIN_CHAT_ID]):
        print("ERROR: Missing TOKEN or ADMIN_CHAT_ID environment variables.")
        return

    application = Application.builder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, forward_message))

    print("Simple bot is starting...")
    application.run_polling()

if __name__ == "__main__":
    main()

