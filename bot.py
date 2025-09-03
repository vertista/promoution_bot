# -*- coding: utf-8 -*-
import os
import re
import asyncio
import psycopg2
import aiohttp
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

# --- CONFIGURATION ---
load_dotenv()

TOKEN = os.getenv("TOKEN")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")
DATABASE_URL = os.getenv("DATABASE_URL")
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")

# --- ASYNCHRONOUS API & SCRAPING ---

async def get_video_stats(url: str) -> dict:
    """
    Асинхронно получает статистику видео, используя aiohttp для TikTok и
    запуская блокирующий вызов YouTube API в отдельном потоке.
    """
    stats = {"platform": "Unknown", "views": "N/A", "likes": "N/A", "comments": "N/A", "error": None}
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}

    try:
        if "tiktok.com" in url:
            stats["platform"] = "TikTok"
            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.get(url, timeout=15) as response:
                    response.raise_for_status()
                    html = await response.text()
                    soup = BeautifulSoup(html, 'html.parser')
                    stats["views"] = soup.find('strong', {'data-e2e': 'view-count'}).text
                    stats["likes"] = soup.find('strong', {'data-e2e': 'like-count'}).text
                    stats["comments"] = soup.find('strong', {'data-e2e': 'comment-count'}).text
        
        elif "youtube.com" in url or "youtu.be" in url:
            stats["platform"] = "YouTube"
            if not YOUTUBE_API_KEY:
                stats["error"] = "YouTube API key not configured."
                return stats
            
            video_id_match = re.search(r"(?:https?:\/\/)?(?:www\.)?(?:youtube\.com|youtu\.be)\/(?:watch\?v=|embed\/|v\/|shorts\/)?([a-zA-Z0-9_-]{11})", url)
            if not video_id_match:
                stats["error"] = "Could not parse YouTube link."
                return stats
            
            video_id = video_id_match.group(1)
            
            loop = asyncio.get_running_loop()
            # Запускаем блокирующий вызов API в отдельном потоке, чтобы не замораживать бота
            response = await loop.run_in_executor(
                None, 
                lambda: build('youtube', 'v3', developerKey=YOUTUBE_API_KEY).videos().list(part="statistics", id=video_id).execute()
            )

            if not response.get('items'):
                stats["error"] = "Video not found or private."
                return stats

            raw_stats = response['items'][0]['statistics']
            stats["views"] = f"{int(raw_stats.get('viewCount', 0)):,}"
            stats["likes"] = f"{int(raw_stats.get('likeCount', 0)):,}"
            stats["comments"] = f"{int(raw_stats.get('commentCount', 0)):,}"
        
        else:
            stats["error"] = "Unsupported link."

    except asyncio.TimeoutError:
        stats["error"] = "Request timed out."
    except Exception as e:
        stats["error"] = f"An error occurred: {e}"
    
    return stats

# --- DATABASE FUNCTIONS ---
def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

def setup_database():
    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute("CREATE TABLE IF NOT EXISTS users (user_id BIGINT PRIMARY KEY, payment_method TEXT, payment_details TEXT);")
        conn.commit()
    conn.close()

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
    keyboard = [[InlineKeyboardButton("💳 Setup Payment", callback_data="setup_payment")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "Welcome! 🚀 Send a video link (TikTok or YouTube) or set up your payment method.",
        reply_markup=reply_markup
    )
    return ConversationHandler.END

async def setup_payment_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    keyboard = [
        [InlineKeyboardButton("💰 Site Balance (Promo Code)", callback_data="payment_promo")],
        [InlineKeyboardButton("💳 Card", callback_data="payment_card")],
        [InlineKeyboardButton("💎 USDT (TRC-20)", callback_data="payment_usdt")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text("Choose your payment method:", reply_markup=reply_markup)
    return SELECTING_METHOD

async def select_payment_method(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    method = query.data.split("_")[1]
    await query.answer()
    if method == "promo":
        save_user_data(query.from_user.id, "Site Balance", "Promo Code will be provided.")
        await query.edit_message_text("✅ Payment method: Site Balance. Now, send your video link.")
        return ConversationHandler.END
    elif method == "card":
        await query.edit_message_text("Enter your card number (16 digits):")
        return TYPING_CARD
    elif method == "usdt":
        await query.edit_message_text("Enter your USDT (TRC-20) address:")
        return TYPING_USDT

async def save_card_details(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    card_number = "".join(filter(str.isdigit, update.message.text))
    if len(card_number) == 16:
        save_user_data(update.effective_user.id, "Card", card_number)
        await update.message.reply_text("✅ Card saved. Send your video link.")
        return ConversationHandler.END
    else:
        await update.message.reply_text("❌ Invalid format. 16 digits required.")
        return TYPING_CARD

async def save_usdt_details(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    usdt_address = update.message.text.strip()
    if usdt_address.startswith("T") and len(usdt_address) == 34:
        save_user_data(update.effective_user.id, "USDT (TRC-20)", usdt_address)
        await update.message.reply_text("✅ Wallet saved. Send your video link.")
        return ConversationHandler.END
    else:
        await update.message.reply_text("❌ Invalid USDT address. Please try again.")
        return TYPING_USDT

# --- SUBMISSION PROCESSING ---
async def animate_loading_bar(message, stop_event: asyncio.Event):
    """Анимация загрузочного бара."""
    total_blocks = 5
    progress = 0
    while not stop_event.is_set():
        filled = progress % (total_blocks + 1)
        bar = "⬛" * filled + "⬜" * (total_blocks - filled)
        percent = (filled / total_blocks) * 100
        try:
            await message.edit_text(f"🔎 Analyzing link...\n[{bar}] {int(percent)}%")
        except TelegramError:
            break
        progress += 1
        await asyncio.sleep(0.6)

async def process_submission_in_background(context: ContextTypes.DEFAULT_TYPE):
    """Фоновая задача для сбора данных и отправки отчета админу."""
    job_data = context.job.data
    user = job_data['user']
    video_url = job_data['video_url']
    user_message_id = job_data['user_message_id']
    user_chat_id = job_data['user_chat_id']

    stats = await get_video_stats(video_url)

    if stats.get('error'):
        stats_text = f"❌ Error: {stats['error']}"
    else:
        stats_text = (
            f"📊 <b>{stats['platform']} Stats</b>\n"
            f"👀 Views: {stats['views']}\n"
            f"👍 Likes: {stats['likes']}\n"
            f"💬 Comments: {stats['comments']}"
        )

    admin_text = (
        f"<b>New Submission</b>\n"
        f"<b>From:</b> {user.mention_html()} (<code>{user.id}</code>)\n"
        f"<b>Link:</b> {video_url}\n\n{stats_text}"
    )
    keyboard = [[
        InlineKeyboardButton("✅ Approve", callback_data=f"approve_{user.id}"),
        InlineKeyboardButton("❌ Decline", callback_data=f"decline_{user.id}"),
    ]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await context.bot.send_message(
        chat_id=ADMIN_CHAT_ID,
        text=admin_text,
        reply_markup=reply_markup,
        parse_mode="HTML"
    )

    try:
        context.application.bot_data[f"stop_{user_message_id}"].set()
        await context.bot.edit_message_text(
            chat_id=user_chat_id,
            message_id=user_message_id,
            text="✅ Thank you! Your submission has been sent for review."
        )
    except (TelegramError, KeyError):
        pass

async def handle_submission(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message_text = update.message.text
    if not ("tiktok.com" in message_text or "youtube.com" in message_text or "youtu.be" in message_text):
        await update.message.reply_text("❌ I only accept TikTok and YouTube links.")
        return

    loading_message = await update.message.reply_text("🔎 Analyzing link...\n[⬜⬜⬜⬜⬜] 0%")

    stop_event = asyncio.Event()
    context.application.bot_data[f"stop_{loading_message.message_id}"] = stop_event

    asyncio.create_task(animate_loading_bar(loading_message, stop_event))
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

# --- BUTTON HANDLER ---
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    original_text = query.message.text_html
    action, user_id_str = query.data.split("_")
    user_id = int(user_id_str)

    if action == "approve":
        await context.bot.send_message(
            chat_id=user_id,
            text="🎉 Your submission has been APPROVED!\n\nFor questions: personet.com@proton.me"
        )
        new_text_for_admin = f"{original_text}\n\n------\n<b>✅ APPROVED by {query.from_user.mention_html()}</b>"
    elif action == "decline":
        await context.bot.send_message(chat_id=user_id, text="😔 Your submission has been declined.")
        new_text_for_admin = f"{original_text}\n\n------\n<b>❌ DECLINED by {query.from_user.mention_html()}</b>"

    await query.edit_message_text(text=new_text_for_admin, parse_mode="HTML", reply_markup=None)

# --- ADMIN COMMANDS ---
async def clear_db_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if str(update.effective_user.id) != ADMIN_CHAT_ID:
        return
    keyboard = [[
        InlineKeyboardButton("⚠️ DELETE ALL DATA", callback_data="clear_db_confirm"),
        InlineKeyboardButton("❌ Cancel", callback_data="clear_db_cancel"),
    ]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("⚠️ WARNING! Delete ALL user data?", reply_markup=reply_markup)

async def clear_db_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if query.data.endswith("confirm"):
        clear_users_table()
        await query.edit_message_text("✅ Database cleared.")
    else:
        await query.edit_message_text("Operation cancelled.")

# --- MAIN FUNCTION ---
def main() -> None:
    if not all([TOKEN, ADMIN_CHAT_ID, DATABASE_URL]):
        print("❌ ERROR: Missing environment variables.")
        return

    setup_database()

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

    print("🤖 Bot is running...")
    application.run_polling()

if __name__ == "__main__":
    main()

