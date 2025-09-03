# -*- coding: utf-8 -*-
import os
import re
import asyncio
import logging
import psycopg2
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from googleapiclient.discovery import build
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

# --- LOGGING ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.DEBUG,
)
logger = logging.getLogger(__name__)

# --- CONFIGURATION ---
load_dotenv()

TOKEN = os.getenv("TOKEN")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")
DATABASE_URL = os.getenv("DATABASE_URL")
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")

# --- API & SCRAPING FUNCTIONS ---
def get_video_stats(url: str) -> dict:
    stats = {"platform": "Unknown", "views": "N/A", "likes": "N/A", "comments": "N/A", "error": None}
    headers = {'User-Agent': 'Mozilla/5.0'}

    try:
        if "tiktok.com" in url:
            stats["platform"] = "TikTok"
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')

            views = soup.find('strong', {'data-e2e': 'view-count'})
            likes = soup.find('strong', {'data-e2e': 'like-count'})
            comments = soup.find('strong', {'data-e2e': 'comment-count'})

            if not views or not likes or not comments:
                stats["error"] = "Could not parse TikTok stats."
                return stats

            stats["views"] = views.text
            stats["likes"] = likes.text
            stats["comments"] = comments.text

        elif "youtube.com" in url or "youtu.be" in url:
            stats["platform"] = "YouTube"
            if not YOUTUBE_API_KEY:
                stats["error"] = "YouTube API key not configured."
                return stats

            video_id_match = re.search(
                r"(?:https?:\/\/)?(?:www\.)?(?:youtube\.com|youtu\.be)\/(?:watch\?v=|embed\/|v\/|shorts\/)?([a-zA-Z0-9_-]{11})",
                url
            )
            if not video_id_match:
                stats["error"] = "Could not parse YouTube link."
                return stats

            video_id = video_id_match.group(1)
            youtube = build('youtube', 'v3', developerKey=YOUTUBE_API_KEY)
            request = youtube.videos().list(part="statistics", id=video_id)
            response = request.execute()

            if not response.get('items'):
                stats["error"] = "Video not found or private."
                return stats

            raw_stats = response['items'][0]['statistics']
            stats["views"] = f"{int(raw_stats.get('viewCount', 0)):,}"
            stats["likes"] = f"{int(raw_stats.get('likeCount', 0)):,}"
            stats["comments"] = f"{int(raw_stats.get('commentCount', 0)):,}"

        else:
            stats["error"] = "Unsupported link."

    except requests.exceptions.Timeout:
        stats["error"] = "Request timed out."
    except Exception as e:
        stats["error"] = f"Error: {str(e)}"

    return stats

# --- DATABASE FUNCTIONS ---
def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

def setup_database():
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    payment_method TEXT,
                    payment_details TEXT
                );
            """)
            conn.commit()
        conn.close()
        logger.info("✅ Database initialized")
    except Exception as e:
        logger.error(f"❌ Database error: {e}")

# --- HELPERS ---
async def animate_loading_message(message, stop_event: asyncio.Event):
    frames = ["🔄", "⏳", "⌛", "🔃", "🌀"]
    i = 0
    while not stop_event.is_set():
        try:
            await message.edit_text(f"Analyzing link... {frames[i % len(frames)]}")
            i += 1
            await asyncio.sleep(0.5)
        except TelegramError:
            break

async def process_submission_in_background(context: ContextTypes.DEFAULT_TYPE):
    job_data = context.job.data
    user = job_data['user']
    video_url = job_data['video_url']
    user_message_id = job_data['user_message_id']
    user_chat_id = job_data['user_chat_id']

    stats = await context.application.run_in_executor(None, get_video_stats, video_url)

    stats_text = "❌ Error: " + stats['error'] if stats.get('error') else (
        f"📊 {stats['platform']} Stats\n"
        f"👀 Views: {stats['views']}\n"
        f"👍 Likes: {stats['likes']}\n"
        f"💬 Comments: {stats['comments']}"
    )

    admin_text = (
        f"<b>New Submission</b>\n<b>User:</b> {user.mention_html()} (<code>{user.id}</code>)\n"
        f"<b>Link:</b> {video_url}\n\n{stats_text}"
    )
    keyboard = [[
        InlineKeyboardButton("✅ Approve", callback_data=f"approve_{user.id}"),
        InlineKeyboardButton("❌ Decline", callback_data=f"decline_{user.id}"),
    ]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=admin_text, reply_markup=reply_markup, parse_mode="HTML")

    try:
        context.application.bot_data[f"stop_{user_message_id}"].set()
        await context.bot.edit_message_text(chat_id=user_chat_id, message_id=user_message_id, text="✅ Submission sent for review!")
    except Exception as e:
        logger.error(f"Could not edit user message: {e}")

# --- HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Welcome! Send me a TikTok or YouTube link for analysis.")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("ℹ️ Commands:\n/start - welcome\n/help - this help\n/ping - check bot status")

async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ Bot is alive!")

async def handle_submission(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message_text = update.message.text
    if not any(x in message_text for x in ("tiktok.com", "youtube.com", "youtu.be")):
        await update.message.reply_text("❌ Only TikTok or YouTube links are accepted.")
        return

    loading_message = await update.message.reply_text("Analyzing link... ⏳")
    stop_event = asyncio.Event()
    context.application.bot_data[f"stop_{loading_message.message_id}"] = stop_event

    asyncio.create_task(animate_loading_message(loading_message, stop_event))
    context.job_queue.run_once(
        process_submission_in_background,
        when=1,
        data={
            'user': update.effective_user,
            'video_url': message_text,
            'user_message_id': loading_message.message_id,
            'user_chat_id': update.effective_chat.id
        },
        name=f"process_{update.effective_message.id}"
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    action, user_id_str = query.data.split("_")
    user_id = int(user_id_str)

    if action == "approve":
        await context.bot.send_message(chat_id=user_id, text="🎉 Your submission has been APPROVED.")
        await query.edit_message_text(text=query.message.text_html + "\n\n✅ Approved", parse_mode="HTML")
    elif action == "decline":
        await context.bot.send_message(chat_id=user_id, text="❌ Your submission has been DECLINED.")
        await query.edit_message_text(text=query.message.text_html + "\n\n❌ Declined", parse_mode="HTML")

# --- MAIN ---
def main() -> None:
    if not all([TOKEN, ADMIN_CHAT_ID, DATABASE_URL]):
        logger.error("❌ Missing environment variables.")
        return

    setup_database()
    application = Application.builder().token(TOKEN).build()

    # команды
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(CommandHandler("ping", ping))

    # обработка ссылок
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_submission))
    application.add_handler(CallbackQueryHandler(button_handler, pattern="^(approve|decline)_"))

    logger.info("🤖 Bot is starting...")
    application.run_polling()

if __name__ == "__main__":
    main()
