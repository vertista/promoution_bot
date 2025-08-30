# -*- coding: utf-8 -*-
import os
import threading
import psycopg2
from flask import Flask
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
TOKEN = os.getenv("TOKEN")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")
DATABASE_URL = os.getenv("DATABASE_URL")

# --- DATABASE FUNCTIONS ---
def get_db_connection():
    conn = psycopg2.connect(DATABASE_URL)
    return conn

def setup_database():
    conn = get_db_connection()
    cur = conn.cursor()
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
    cur.close()
    conn.close()

def save_user_data(user_id, method, details):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO users (user_id, payment_method, payment_details) VALUES (%s, %s, %s) "
        "ON CONFLICT (user_id) DO UPDATE SET payment_method = %s, payment_details = %s;",
        (user_id, method, details, method, details),
    )
    conn.commit()
    cur.close()
    conn.close()

# --- BOT STATES FOR CONVERSATION ---
SELECTING_METHOD, TYPING_DETAILS = range(2)

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
    context.user_data["payment_method"] = method
    await query.answer()

    if method == "promo":
        save_user_data(query.from_user.id, "Site Balance", "Promo Code will be provided.")
        await query.edit_message_text(
            "Great! Your payment method is set to 'Site Balance'. "
            "If your submission is approved, we will provide a promo code. "
            "You can now send the link to your video."
        )
        return ConversationHandler.END
    else:
        prompt_text = ""
        if method == "card":
            prompt_text = "Please enter your Russian card number:"
        elif method == "usdt":
            prompt_text = "Please enter your USDT (TRC-20) wallet address:"
        
        await query.edit_message_text(prompt_text)
        return TYPING_DETAILS

async def save_payment_details(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    details = update.message.text
    method = context.user_data.get("payment_method")
    
    method_map = {
        "card": "Russian Card",
        "usdt": "USDT (TRC-20)"
    }
    method_full_name = method_map.get(method, "Unknown")

    save_user_data(user_id, method_full_name, details)
    
    await update.message.reply_text(
        "Thank you! Your payment details have been saved. "
        "You can now send the link to your video."
    )
    return ConversationHandler.END

async def handle_submission(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message_text = update.message.text

    admin_message_text = (
        f"New submission from user: {user.mention_html()} (ID: `{user.id}`)\n\n"
        f"Message: {message_text}"
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

    await update.message.reply_text(
        "Thank you! Your submission has been sent for review. "
        "Please be patient, this may take some time."
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    action, user_id_str = query.data.split("_")
    user_id = int(user_id_str)

    if action == "approve":
        response_text = "Congratulations! Your submission has been APPROVED."
        await query.edit_message_text(text=f"✅ SUBMISSION APPROVED for user {user_id}.")
    elif action == "decline":
        response_text = "We are sorry, but your submission has been DECLINED."
        await query.edit_message_text(text=f"❌ SUBMISSION DECLINED for user {user_id}.")
    
    await context.bot.send_message(chat_id=user_id, text=response_text)

# --- FLASK WEB SERVER FOR RENDER HEALTH CHECKS ---
app = Flask(__name__)

@app.route("/")
def index():
    return "Bot is alive!"

def run_flask():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))

# --- MAIN FUNCTION ---
def main() -> None:
    if not all([TOKEN, ADMIN_CHAT_ID, DATABASE_URL]):
        print("ERROR: Missing one or more environment variables (TOKEN, ADMIN_CHAT_ID, DATABASE_URL).")
        return

    print("Setting up database...")
    setup_database()
    print("Database setup complete.")

    application = Application.builder().token(TOKEN).build()
    
    conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(setup_payment_start, pattern='^setup_payment$')],
        states={
            SELECTING_METHOD: [CallbackQueryHandler(select_payment_method, pattern='^payment_')],
            TYPING_DETAILS: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_payment_details)],
        },
        fallbacks=[CommandHandler('start', start)],
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(conv_handler)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_submission))
    application.add_handler(CallbackQueryHandler(button_handler, pattern='^(approve|decline)_'))

    # Start Flask server in a separate thread
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.start()

    print("Bot is starting...")
    application.run_polling()

if __name__ == "__main__":
    main()

