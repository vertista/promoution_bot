# -*- coding: utf-8 -*-
import os
import re
import threading
import psycopg2
from psycopg2 import pool # Импортируем пул соединений
from flask import Flask
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

# --- CONFIGURATION ---
load_dotenv()

TOKEN = os.getenv("TOKEN")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")
DATABASE_URL = os.getenv("DATABASE_URL")

# Глобальная переменная для пула соединений
db_pool = None

# --- VALIDATION FUNCTIONS ---

def is_valid_video_link(text: str) -> bool:
    """Проверяет, является ли текст ссылкой на TikTok или YouTube Shorts."""
    return "tiktok.com" in text or "youtube.com/shorts/" in text

def is_valid_russian_card(card_number: str) -> bool:
    """Проверяет номер карты РФ (16 цифр) с помощью алгоритма Луна."""
    if not re.fullmatch(r"\d{16}", card_number):
        return False
    digits = [int(d) for d in card_number]
    checksum = 0
    for i, digit in enumerate(digits):
        if i % 2 == 0:
            doubled_digit = digit * 2
            if doubled_digit > 9:
                doubled_digit -= 9
            checksum += doubled_digit
        else:
            checksum += digit
    return checksum % 10 == 0

def is_valid_usdt_address(address: str) -> bool:
    """Проверяет базовый формат адреса USDT TRC-20."""
    return re.fullmatch(r"T[a-zA-Z0-9]{33}", address) is not None


# --- DATABASE FUNCTIONS (ОБНОВЛЕНО ДЛЯ РАБОТЫ С ПУЛОМ) ---
def setup_database():
    """Создает таблицу users, если она не существует, используя пул соединений."""
    conn = None
    try:
        conn = db_pool.getconn()
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    payment_method TEXT,
                    payment_details TEXT
                );
            """
            )
            conn.commit()
    finally:
        if conn:
            db_pool.putconn(conn)

def save_user_data(user_id, method, details):
    """Сохраняет или обновляет платежные данные пользователя, используя пул соединений."""
    conn = None
    try:
        conn = db_pool.getconn()
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO users (user_id, payment_method, payment_details) VALUES (%s, %s, %s) "
                "ON CONFLICT (user_id) DO UPDATE SET payment_method = %s, payment_details = %s;",
                (user_id, method, details, method, details),
            )
            conn.commit()
    finally:
        if conn:
            db_pool.putconn(conn)

def clear_users_table():
    """Полностью очищает таблицу users, используя пул соединений."""
    conn = None
    try:
        conn = db_pool.getconn()
        with conn.cursor() as cur:
            cur.execute("TRUNCATE TABLE users;")
            conn.commit()
    finally:
        if conn:
            db_pool.putconn(conn)

# --- BOT STATES FOR CONVERSATION ---
SELECTING_METHOD, TYPING_CARD, TYPING_USDT = range(3)

# --- BOT HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    keyboard = [
        [InlineKeyboardButton("Setup Payment Details", callback_data="setup_payment")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "Welcome! I am your assistant for the promo event.\n\n"
        "Please set up your payment details before submitting a video. "
        "You can also send a link to your video directly.",
        reply_markup=reply_markup,
    )
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
    await query.edit_message_text(
        "Please choose your preferred payment method:", reply_markup=reply_markup
    )
    return SELECTING_METHOD

async def select_payment_method(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    method = query.data.split("_")[1]
    await query.answer()

    if method == "promo":
        save_user_data(query.from_user.id, "Site Balance", "Promo Code will be provided.")
        await query.edit_message_text(
            "Great! Your payment method is set to 'Site Balance'. "
            "You can now send the link to your video."
        )
        return ConversationHandler.END
    else:
        if method == "card":
            await query.edit_message_text("Please enter your Russian card number (16 digits):")
            return TYPING_CARD
        elif method == "usdt":
            await query.edit_message_text("Please enter your USDT (TRC-20) wallet address:")
            return TYPING_USDT

async def save_card_details(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Сохраняет номер карты после валидации."""
    # ИСПРАВЛЕНИЕ: Удаляем пробелы и дефисы из введенного номера
    card_number = re.sub(r'[\s-]', '', update.message.text)
    if is_valid_russian_card(card_number):
        save_user_data(update.effective_user.id, "Russian Card", card_number)
        await update.message.reply_text("Thank you! Your card number has been saved. You can now send your video link.")
        return ConversationHandler.END
    else:
        await update.message.reply_text("Invalid card number. Please enter 16 digits and try again.")
        return TYPING_CARD

async def save_usdt_details(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Сохраняет адрес USDT после валидации."""
    usdt_address = update.message.text.strip()
    if is_valid_usdt_address(usdt_address):
        save_user_data(update.effective_user.id, "USDT (TRC-20)", usdt_address)
        await update.message.reply_text("Thank you! Your wallet address has been saved. You can now send your video link.")
        return ConversationHandler.END
    else:
        await update.message.reply_text("Invalid USDT address. It should start with 'T' and be 34 characters long. Please try again.")
        return TYPING_USDT

async def handle_submission(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message_text = update.message.text
    if not is_valid_video_link(message_text):
        await update.message.reply_text("Sorry, I only accept links from TikTok and YouTube Shorts.")
        return
    
    user = update.effective_user
    admin_message_text = (
        f"New submission from user: {user.mention_html()} (ID: `{user.id}`)\n\n"
        f"Link: {message_text}"
    )
    keyboard = [
        [
            InlineKeyboardButton("✅ Approve", callback_data=f"approve_{user.id}"),
            InlineKeyboardButton("❌ Decline", callback_data=f"decline_{user.id}"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await context.bot.send_message(
        chat_id=ADMIN_CHAT_ID, text=admin_message_text, reply_markup=reply_markup, parse_mode="HTML"
    )
    await update.message.reply_text("Thank you! Your submission has been sent for review.")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    action, user_id_str = query.data.split("_")
    user_id = int(user_id_str)
    if action == "approve":
        response_text = "Congratulations! Your submission has been APPROVED."
        await query.edit_message_text(text=f"✅ SUBMISSION APPROVED for user {user_id}.")
    else:
        response_text = "We are sorry, but your submission has been DECLINED."
        await query.edit_message_text(text=f"❌ SUBMISSION DECLINED for user {user_id}.")
    await context.bot.send_message(chat_id=user_id, text=response_text)

# --- ADMIN COMMANDS ---
async def clear_db_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда для очистки базы данных (только для админа)."""
    if str(update.effective_user.id) != ADMIN_CHAT_ID:
        await update.message.reply_text("You are not authorized to use this command.")
        return

    keyboard = [
        [
            InlineKeyboardButton("YES, I am sure", callback_data="clear_db_confirm"),
            InlineKeyboardButton("NO, cancel", callback_data="clear_db_cancel"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "⚠️ WARNING! Are you sure you want to delete ALL user data from the database? This action cannot be undone.",
        reply_markup=reply_markup
    )

async def clear_db_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик подтверждения очистки базы."""
    query = update.callback_query
    await query.answer()
    action = query.data.split("_")[-1]

    if action == "confirm":
        clear_users_table()
        await query.edit_message_text("✅ Database has been cleared successfully.")
    else:
        await query.edit_message_text("Database clearing operation cancelled.")

# --- FLASK WEB SERVER ---
app = Flask(__name__)
@app.route("/")
def index():
    return "Bot is alive!"
def run_flask():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))

# --- MAIN FUNCTION ---
def main() -> None:
    """Основная функция для запуска бота."""
    global db_pool # Объявляем, что будем изменять глобальную переменную
    
    if not all([TOKEN, ADMIN_CHAT_ID, DATABASE_URL]):
        print("ERROR: Missing one or more environment variables.")
        return
    
    print("Creating database connection pool...")
    # Создаем пул соединений при запуске бота
    db_pool = pool.SimpleConnectionPool(1, 5, dsn=DATABASE_URL)
    
    print("Setting up database...")
    setup_database()
    print("Database setup complete.")

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

    flask_thread = threading.Thread(target=run_flask)
    flask_thread.start()
    print("Bot is starting...")
    application.run_polling()

if __name__ == "__main__":
    main()