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
    stats = {"platform": "Unknown", "views": "N/A", "likes": "N/A", "comments": "N/A", "error": None}
    headers = {
        'User-Agent': (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/91.0.4472.124 Safari/537.36'
        )
    }

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

            video_id_match = re.search(
                r"(?:https?:\/\/)?(?:www\.)?(?:youtube\.com|youtu\.be)\/"
                r"(?:watch\?v=|embed\/|v\/|shorts\/)?([a-zA-Z0-9_-]{11})",
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
        stats["error"] = f"An error occurred: {str(e)}"

    return stats


# --- DATABASE FUNCTIONS ---
def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

def setup_database():
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

def save_user_data(user_id, method, details):
    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO users (user_id, payment_method, payment_details)
            VALUES (%s, %s, %s)
            ON CONFLICT (user_id)
            DO UPDATE SET payment_method = %s, payment_details = %s;
            """,
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
    keyboard = [[InlineKeyboardButton("💳 Настроить оплату", callback_data="setup_payment")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "Привет! 🚀 Отправь ссылку на видео (TikTok или YouTube) или настрой способ оплаты.",
        reply_markup=reply_markup
    )
    return ConversationHandler.END


async def setup_payment_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    keyboard = [
        [InlineKeyboardButton("💰 Баланс сайта (Промокод)", callback_data="payment_promo")],
        [InlineKeyboardButton("💳 Российская карта", callback_data="payment_card")],
        [InlineKeyboardButton("💎 USDT (TRC-20)", callback_data="payment_usdt")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text("Выбери способ оплаты:", reply_markup=reply_markup)
    return SELECTING_METHOD


async def select_payment_method(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    method = query.data.split("_")[1]
    await query.answer()
    if method == "promo":
        save_user_data(query.from_user.id, "Site Balance", "Promo Code will be provided.")
        await query.edit_message_text("✅ Метод оплаты: Баланс сайта. Теперь отправь ссылку на видео.")
        return ConversationHandler.END
    elif method == "card":
        await query.edit_message_text("Введите номер карты (16 цифр):")
        return TYPING_CARD
    elif method == "usdt":
        await query.edit_message_text("Введите USDT (TRC-20) адрес:")
        return TYPING_USDT


async def save_card_details(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    card_number = "".join(filter(str.isdigit, update.message.text))
    if len(card_number) == 16:
        save_user_data(update.effective_user.id, "Russian Card", card_number)
        await update.message.reply_text("✅ Карта сохранена. Отправь ссылку на видео.")
        return ConversationHandler.END
    else:
        await update.message.reply_text("❌ Неверный формат. Нужно 16 цифр.")
        return TYPING_CARD


async def save_usdt_details(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    usdt_address = update.message.text.strip()
    if usdt_address.startswith("T") and len(usdt_address) == 34:
        save_user_data(update.effective_user.id, "USDT (TRC-20)", usdt_address)
        await update.message.reply_text("✅ Кошелёк сохранён. Отправь ссылку на видео.")
        return ConversationHandler.END
    else:
        await update.message.reply_text("❌ Неверный адрес USDT. Попробуйте снова.")
        return TYPING_USDT


# --- LOADING BAR ---
async def animate_loading_bar(message, stop_event: asyncio.Event):
    """Анимация загрузочного бара."""
    total_blocks = 5
    progress = 0
    while not stop_event.is_set():
        filled = progress % (total_blocks + 1)
        bar = "⬛" * filled + "⬜" * (total_blocks - filled)
        percent = (filled / total_blocks) * 100
        try:
            await message.edit_text(f"🔎 Анализирую ссылку...\n[{bar}] {int(percent)}%")
        except TelegramError:
            break
        progress += 1
        await asyncio.sleep(0.6)


# --- BACKGROUND PROCESS ---
async def process_submission_in_background(context: ContextTypes.DEFAULT_TYPE):
    job_data = context.job.data
    user = job_data['user']
    video_url = job_data['video_url']
    user_message_id = job_data['user_message_id']
    user_chat_id = job_data['user_chat_id']

    stats = await context.application.run_in_executor(None, get_video_stats, video_url)

    if stats.get('error'):
        stats_text = f"❌ Ошибка: {stats['error']}"
    else:
        stats_text = (
            f"📊 <b>{stats['platform']} Статистика</b>\n"
            f"👀 Просмотры: {stats['views']}\n"
            f"👍 Лайки: {stats['likes']}\n"
            f"💬 Комментарии: {stats['comments']}"
        )

    admin_text = (
        f"<b>Новая заявка</b>\n"
        f"<b>От:</b> {user.mention_html()} (<code>{user.id}</code>)\n"
        f"<b>Ссылка:</b> {video_url}\n\n{stats_text}"
    )
    keyboard = [[
        InlineKeyboardButton("✅ Одобрить", callback_data=f"approve_{user.id}"),
        InlineKeyboardButton("❌ Отклонить", callback_data=f"decline_{user.id}"),
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
            text="✅ Спасибо! Ваша заявка отправлена на проверку."
        )
    except (TelegramError, KeyError):
        pass


# --- SUBMISSION HANDLER ---
async def handle_submission(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message_text = update.message.text
    if not ("tiktok.com" in message_text or "youtube.com" in message_text or "youtu.be" in message_text):
        await update.message.reply_text("❌ Принимаю только ссылки TikTok и YouTube.")
        return

    loading_message = await update.message.reply_text("🔎 Анализирую ссылку...\n[⬜⬜⬜⬜⬜] 0%")

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
            text="🎉 Ваша заявка ОДОБРЕНА!\n\nСвяжитесь с нами при необходимости: personet.com@proton.me"
        )
        new_text_for_admin = f"{original_text}\n\n------\n<b>✅ ОДОБРЕНО {query.from_user.mention_html()}</b>"
    elif action == "decline":
        await context.bot.send_message(
            chat_id=user_id,
            text="😔 Ваша заявка отклонена."
        )
        new_text_for_admin = f"{original_text}\n\n------\n<b>❌ ОТКЛОНЕНО {query.from_user.mention_html()}</b>"

    await query.edit_message_text(text=new_text_for_admin, parse_mode="HTML", reply_markup=None)


# --- ADMIN COMMANDS ---
async def clear_db_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if str(update.effective_user.id) != ADMIN_CHAT_ID:
        return
    keyboard = [[
        InlineKeyboardButton("⚠️ УДАЛИТЬ ВСЕ ДАННЫЕ", callback_data="clear_db_confirm"),
        InlineKeyboardButton("❌ Отмена", callback_data="clear_db_cancel"),
    ]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "⚠️ ВНИМАНИЕ! Удалить ВСЕ данные пользователей?",
        reply_markup=reply_markup
    )

async def clear_db_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if query.data.endswith("confirm"):
        clear_users_table()
        await query.edit_message_text("✅ База данных очищена.")
    else:
        await query.edit_message_text("Операция отменена.")


# --- MAIN FUNCTION ---
def main() -> None:
    if not all([TOKEN, ADMIN_CHAT_ID, DATABASE_URL]):
        print("❌ Ошибка: отсутствуют переменные окружения.")
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

    print("🤖 Бот запущен...")
    application.run_polling()


if __name__ == "__main__":
    main()
