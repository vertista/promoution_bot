# -*- coding: utf-8 -*-
import os
import re
import threading
import time
import asyncio
import psycopg2
import requests
from bs4 import BeautifulSoup
from flask import Flask
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

# --- CONFIGURATION ---
load_dotenv()

TOKEN = os.getenv("TOKEN")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")
DATABASE_URL = os.getenv("DATABASE_URL")
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")

# --- ГЛОБАЛЬНЫЙ ФЛАГ ГОТОВНОСТИ БАЗЫ ДАННЫХ ---
DB_READY = False

# --- API & SCRAPING FUNCTIONS ---

def get_youtube_video_stats(video_id: str) -> str:
    """Получает статистику видео с YouTube по его ID."""
    if not YOUTUBE_API_KEY:
        return "YouTube API key is not configured."
    try:
        youtube = build('youtube', 'v3', developerKey=YOUTUBE_API_KEY)
        request = youtube.videos().list(part="statistics", id=video_id)
        response = request.execute()
        
        if not response.get('items'):
            return "Video not found or private."

        stats = response['items'][0]['statistics']
        views = int(stats.get('viewCount', 0))
        likes = int(stats.get('likeCount', 0))
        comments = int(stats.get('commentCount', 0))
        
        return (
            f"📊 <b>YouTube Stats</b>\n"
            f"👀 Views: {views:,}\n"
            f"👍 Likes: {likes:,}\n"
            f"💬 Comments: {comments:,}"
        )
    except Exception as e:
        print(f"YouTube API Error: {e}")
        return "Could not fetch YouTube stats."

def get_tiktok_video_stats(url: str) -> str:
    """Получает статистику видео с TikTok с жестким таймаутом."""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        
        views_tag = soup.find('strong', {'data-e2e': 'view-count'})
        likes_tag = soup.find('strong', {'data-e2e': 'like-count'})
        comments_tag = soup.find('strong', {'data-e2e': 'comment-count'})
        
        views = views_tag.text if views_tag else 'N/A'
        likes = likes_tag.text if likes_tag else 'N/A'
        comments = comments_tag.text if comments_tag else 'N/A'

        return (
            f"📊 <b>TikTok Stats</b>\n"
            f"👀 Views: {views}\n"
            f"👍 Likes: {likes}\n"
            f"💬 Comments: {comments}"
        )
    except requests.exceptions.Timeout:
        print("TikTok Scraping Error: Request timed out.")
        return "Could not fetch TikTok stats: request timed out."
    except Exception as e:
        print(f"TikTok Scraping Error: {e}")
        return "Could not fetch TikTok stats (might be private or page layout changed)."

def extract_youtube_id(url: str):
    """Извлекает ID видео из ссылки YouTube."""
    regex = r"(?:https?:\/\/)?(?:www\.)?(?:youtube\.com|youtu\.be)\/(?:watch\?v=|embed\/|v\/|shorts\/)?([a-zA-Z0-9_-]{11})"
    match = re.search(regex, url)
    return match.group(1) if match else None

def get_stats_blocking(url: str) -> str:
    """Блокирующая функция для получения статистики."""
    if "tiktok.com" in url:
        return get_tiktok_video_stats(url)
    else:
        video_id = extract_youtube_id(url)
        if video_id:
            return get_youtube_video_stats(video_id)
        else:
            return "Could not parse YouTube link."

# --- DATABASE FUNCTIONS ---
def get_db_connection():
    conn = psycopg2.connect(DATABASE_URL)
    return conn

def setup_database_in_background():
    global DB_READY
    print("Starting background database setup...")
    while not DB_READY:
        try:
            conn = get_db_connection()
            with conn.cursor() as cur:
                cur.execute("CREATE TABLE IF NOT EXISTS users (user_id BIGINT PRIMARY KEY, payment_method TEXT, payment_details TEXT);")
                conn.commit()
            conn.close()
            DB_READY = True
            print("✅ Background database setup complete.")
        except Exception as e:
            print(f"Error setting up database: {e}. Retrying in 5 seconds...")
            time.sleep(5)

def save_user_data(user_id, method, details):
    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO users (user_id, payment_method, payment_details) VALUES (%s, %s, %s) "
            "ON CONFLICT (user_id) DO UPDATE SET payment_method = %s, payment_details = %s;",
            (user_id, method, details, method, details),
        )
        conn.commit()
    conn.close()

def clear_users_table():
    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute("TRUNCATE TABLE users;")
        conn.commit()
    conn.close()

# --- BOT STATES ---
SELECTING_METHOD, TYPING_CARD, TYPING_USDT = range(3)

# --- BOT HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    keyboard = [[InlineKeyboardButton("Setup Payment Details", callback_data="setup_payment")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Welcome! Please set up your payment details or send your video link.", reply_markup=reply_markup)
    return ConversationHandler.END

async def setup_payment_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if not DB_READY:
        await query.edit_message_text("The database is warming up. Please try again in a few seconds.")
        return ConversationHandler.END
    keyboard = [
        [InlineKeyboardButton("Site Balance (Promo Code)", callback_data="payment_promo")],
        [InlineKeyboardButton("Russian Card", callback_data="payment_card")],
        [InlineKeyboardButton("USDT (TRC-20)", callback_data="payment_usdt")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text("Please choose your preferred payment method:", reply_markup=reply_markup)
    return SELECTING_METHOD

async def select_payment_method(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    method = query.data.split("_")[1]
    await query.answer()
    if method == "promo":
        save_user_data(query.from_user.id, "Site Balance", "Promo Code will be provided.")
        await query.edit_message_text("Great! Payment method set to 'Site Balance'. You can now send your video link.")
        return ConversationHandler.END
    else:
        if method == "card":
            await query.edit_message_text("Please enter your Russian card number (16 digits):")
            return TYPING_CARD
        elif method == "usdt":
            await query.edit_message_text("Please enter your USDT (TRC-20) wallet address:")
            return TYPING_USDT

async def save_card_details(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    card_number = "".join(filter(str.isdigit, update.message.text))
    if len(card_number) == 16:
        save_user_data(update.effective_user.id, "Russian Card", card_number)
        await update.message.reply_text("Thank you! Your card number is saved. You can now send your video link.")
        return ConversationHandler.END
    else:
        await update.message.reply_text("Invalid format. Please enter exactly 16 digits and try again.")
        return TYPING_CARD

async def save_usdt_details(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    usdt_address = update.message.text.strip()
    if usdt_address.startswith("T") and len(usdt_address) == 34:
        save_user_data(update.effective_user.id, "USDT (TRC-20)", usdt_address)
        await update.message.reply_text("Thank you! Your wallet address is saved. You can now send your video link.")
        return ConversationHandler.END
    else:
        await update.message.reply_text("Invalid USDT address format. Please try again.")
        return TYPING_USDT

async def fetch_and_update_stats(context: ContextTypes.DEFAULT_TYPE):
    """Фоновая задача для получения статистики и обновления сообщения админа."""
    job_context = context.job.data
    user = job_context["user"]
    message_text = job_context["message_text"]
    admin_message_id = job_context["admin_message_id"]

    stats_text = await context.application.run_in_executor(
        None, get_stats_blocking, message_text
    )

    new_admin_text = (
        f"<b>New Submission</b>\n\n"
        f"<b>From:</b> {user.mention_html()} (<code>{user.id}</code>)\n"
        f"<b>Video Link:</b> <a href=\"{message_text}\">Click to watch</a>\n\n"
        f"--------------------\n"
        f"{stats_text}"
    )
    keyboard = [[
        InlineKeyboardButton("✅ Approve", callback_data=f"approve_{user.id}"),
        InlineKeyboardButton("❌ Decline", callback_data=f"decline_{user.id}"),
    ]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    try:
        await context.bot.edit_message_text(
            chat_id=ADMIN_CHAT_ID,
            message_id=admin_message_id,
            text=new_admin_text,
            reply_markup=reply_markup,
            parse_mode="HTML"
        )
    except TelegramError as e:
        print(f"Could not edit admin message: {e}")

async def handle_submission(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message_text = update.message.text
    if not ("tiktok.com" in message_text or "youtube.com" in message_text or "youtu.be" in message_text):
        await update.message.reply_text("Sorry, I only accept links from TikTok and YouTube.")
        return

    user = update.effective_user
    
    initial_admin_text = (
        f"<b>New Submission</b>\n\n"
        f"<b>From:</b> {user.mention_html()} (<code>{user.id}</code>)\n"
        f"<b>Video Link:</b> <a href=\"{message_text}\">Click to watch</a>\n\n"
        f"--------------------\n"
        f"⏳ Fetching stats..."
    )
    admin_message = await context.bot.send_message(
        chat_id=ADMIN_CHAT_ID, text=initial_admin_text, parse_mode="HTML"
    )

    context.job_queue.run_once(
        fetch_and_update_stats, 
        when=1,
        data={
            "user": user,
            "message_text": message_text,
            "admin_message_id": admin_message.message_id
        },
        name=f"stats_{user.id}_{admin_message.message_id}"
    )
    
    await update.message.reply_text("Thank you! Your submission has been sent for review.")


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    original_text = query.message.text_html
    action, user_id_str = query.data.split("_")
    user_id = int(user_id_str)

    if action == "approve":
        response_text_to_user = "Congratulations! Your submission has been APPROVED.\n\nIf you have any questions, please contact us at: personet.com@proton.me"
        new_text_for_admin = f"{original_text}\n\n------\n<b>✅ STATUS: APPROVED by {query.from_user.mention_html()}</b>"
        
        await query.edit_message_text(text=new_text_for_admin, parse_mode="HTML", reply_markup=None)
        await context.bot.send_message(chat_id=user_id, text=response_text_to_user)
    
    elif action == "decline":
        response_text_to_user = "We are sorry, but your submission has been DECLINED."
        new_text_for_admin = f"{original_text}\n\n------\n<b>❌ STATUS: DECLINED by {query.from_user.mention_html()}</b>"
        
        await query.edit_message_text(text=new_text_for_admin, parse_mode="HTML", reply_markup=None)
        await context.bot.send_message(chat_id=user_id, text=response_text_to_user)

# --- ADMIN COMMANDS ---
async def clear_db_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if str(update.effective_user.id) != ADMIN_CHAT_ID: return
    keyboard = [[
        InlineKeyboardButton("YES, delete all data", callback_data="clear_db_confirm"),
        InlineKeyboardButton("NO, cancel", callback_data="clear_db_cancel"),
    ]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("⚠️ WARNING! Are you sure you want to delete ALL user data? This cannot be undone.", reply_markup=reply_markup)

async def clear_db_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if query.data.endswith("confirm"):
        clear_users_table()
        await query.edit_message_text("✅ Database has been cleared.")
    else:
        await query.edit_message_text("Operation cancelled.")

# --- FLASK WEB SERVER ---
app = Flask(__name__)
@app.route("/")
def index(): return "Bot is alive!"
def run_flask(): app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))

# --- MAIN FUNCTION ---
def main() -> None:
    if not all([TOKEN, ADMIN_CHAT_ID, DATABASE_URL]):
        print("ERROR: Missing one or more environment variables.")
        return

    application = Application.builder().token(TOKEN).build()
    
    conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(setup_payment_start, pattern='^setup_payment$')],
        states={
            SELECTING_METHOD: [CallbackQueryHandler(select_payment_method, pattern='^payment_')],
            TYPING_CARD: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_card_details)],
            TYPING_USDT: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_usdt_details)],
        },
        fallbacks=[CommandHandler('start', start)],
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("clear_db", clear_db_command))
    application.add_handler(conv_handler)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_submission))
    application.add_handler(CallbackQueryHandler(button_handler, pattern='^(approve|decline)_'))
    application.add_handler(CallbackQueryHandler(clear_db_confirm, pattern='^clear_db_'))

    db_thread = threading.Thread(target=setup_database_in_background)
    db_thread.start()
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.start()
    
    print("Bot is starting polling immediately...")
    application.run_polling()

if __name__ == "__main__":
    main()

