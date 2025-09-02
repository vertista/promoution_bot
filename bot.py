# -*- coding: utf-8 -*-
import os
import re
import asyncio
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

# --- CONFIGURATION ---
load_dotenv()

TOKEN = os.getenv("TOKEN")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")
DATABASE_URL = os.getenv("DATABASE_URL")
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")

# --- API & SCRAPING FUNCTIONS ---

def get_video_stats(url: str) -> dict:
    """
    Универсальная функция для сбора статистики.
    Возвращает словарь с данными или ошибкой.
    """
    stats = {"platform": "Unknown", "views": "N/A", "likes": "N/A", "comments": "N/A", "error": None}
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}

    try:
        if "tiktok.com" in url:
            stats["platform"] = "TikTok"
            response = requests.get(url, headers=headers, timeout=15)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')
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
        stats["error"] = f"An error occurred: {str(e)}"
    
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
    keyboard = [[InlineKeyboardButton("Setup Payment Details", callback_data="setup_payment")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Welcome! Please set up your payment details or send your video link.", reply_markup=reply_markup)
    return ConversationHandler.END

async def setup_payment_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
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

async def animate_loading_message(message: Update.message, stop_event: asyncio.Event):
    """Анимирует сообщение для пользователя."""
    animation_frames = ["⢿", "⣻", "⣽", "⣾", "⣷", "⣯", "⣟", "⡿"]
    i = 0
    while not stop_event.is_set():
        try:
            await message.edit_text(f"Analyzing link... {animation_frames[i % len(animation_frames)]}")
            i += 1
            await asyncio.sleep(0.2)
        except TelegramError:
            break

async def process_submission_in_background(context: ContextTypes.DEFAULT_TYPE):
    """Фоновая задача для сбора данных и отправки отчета админу."""
    job_data = context.job.data
    user = job_data['user']
    video_url = job_data['video_url']
    user_message_id = job_data['user_message_id']
    user_chat_id = job_data['user_chat_id']

    # Запускаем сбор данных в отдельном потоке, чтобы не блокировать асинхронный код
    stats = await context.application.run_in_executor(None, get_video_stats, video_url)
    
    # Готовим отчет для админа
    stats_text = ""
    if stats.get('error'):
        stats_text = f"❌ <b>Error:</b> {stats['error']}"
    else:
        stats_text = (
            f"📊 <b>{stats['platform']} Stats</b> [✅ OK]\n"
            f"👀 Views: {stats['views']}\n"
            f"👍 Likes: {stats['likes']}\n"
            f"💬 Comments: {stats['comments']}"
        )
    
    admin_text = (
        f"<b>New Submission</b>\n<b>From:</b> {user.mention_html()} (<code>{user.id}</code>)\n"
        f"<b>Link:</b> {video_url}\n\n{stats_text}"
    )
    keyboard = [[
        InlineKeyboardButton("✅ Approve", callback_data=f"approve_{user.id}"),
        InlineKeyboardButton("❌ Decline", callback_data=f"decline_{user.id}"),
    ]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    # Отправляем финальный отчет админу
    await context.bot.send_message(
        chat_id=ADMIN_CHAT_ID,
        text=admin_text,
        reply_markup=reply_markup,
        parse_mode="HTML"
    )

    # Уведомляем пользователя, что все готово
    try:
        # Сначала нужно остановить анимацию, отправив событие
        context.application.bot_data[f"stop_{user_message_id}"].set()
        # Редактируем сообщение пользователя
        await context.bot.edit_message_text(
            chat_id=user_chat_id,
            message_id=user_message_id,
            text="Thank you! Your submission has been sent for review."
        )
    except (TelegramError, KeyError) as e:
        print(f"Could not edit user message: {e}")


async def handle_submission(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message_text = update.message.text
    if not ("tiktok.com" in message_text or "youtube.com" in message_text or "youtu.be" in message_text):
        await update.message.reply_text("Sorry, I only accept links from TikTok and YouTube.")
        return

    # 1. Мгновенно отправляем пользователю сообщение с анимацией
    loading_message = await update.message.reply_text("Analyzing link... ⢿")

    # 2. Создаем событие для остановки анимации
    stop_event = asyncio.Event()
    context.application.bot_data[f"stop_{loading_message.message_id}"] = stop_event

    # 3. Запускаем саму анимацию и сбор данных в фоновом режиме
    asyncio.create_task(animate_loading_message(loading_message, stop_event))
    context.job_queue.run_once(
        process_submission_in_background,
        when=1, # Запустить почти сразу
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
    original_text = query.message.text_html
    action, user_id_str = query.data.split("_")
    user_id = int(user_id_str)

    if action == "approve":
        response_text_to_user = "Congratulations! Your submission has been APPROVED.\n\nIf you have any questions, contact us: personet.com@proton.me"
        new_text_for_admin = f"{original_text}\n\n------\n<b>✅ STATUS: APPROVED by {query.from_user.mention_html()}</b>"
        await context.bot.send_message(chat_id=user_id, text=response_text_to_user)
    elif action == "decline":
        response_text_to_user = "We are sorry, but your submission has been DECLINED."
        new_text_for_admin = f"{original_text}\n\n------\n<b>❌ STATUS: DECLINED by {query.from_user.mention_html()}</b>"
        await context.bot.send_message(chat_id=user_id, text=response_text_to_user)
        
    await query.edit_message_text(text=new_text_for_admin, parse_mode="HTML", reply_markup=None)

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

# --- MAIN FUNCTION ---
def main() -> None:
    if not all([TOKEN, ADMIN_CHAT_ID, DATABASE_URL]):
        print("ERROR: Missing one or more environment variables.")
        return

    # Упрощенная настройка без Flask
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
    
    print("Bot is starting polling...")
    application.run_polling()

if __name__ == "__main__":
    main()

