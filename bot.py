import os
import psycopg2
from urllib.parse import urlparse
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
TOKEN = os.getenv('TELEGRAM_TOKEN')
ADMIN_CHAT_ID = os.getenv('ADMIN_CHAT_ID')
DATABASE_URL = os.getenv('DATABASE_URL')

# --- DATABASE HELPER FUNCTIONS ---

def get_db_connection():
    """Establishes a connection to the database."""
    result = urlparse(DATABASE_URL)
    conn = psycopg2.connect(
        dbname=result.path[1:],
        user=result.username,
        password=result.password,
        host=result.hostname,
        port=result.port
    )
    return conn

def setup_database():
    """Creates the users table if it doesn't exist."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            payment_method TEXT,
            payment_details TEXT
        );
    ''')
    conn.commit()
    cur.close()
    conn.close()

def save_user_details(user_id, method, details):
    """Saves or updates user payment details."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO users (user_id, payment_method, payment_details) VALUES (%s, %s, %s) "
        "ON CONFLICT (user_id) DO UPDATE SET payment_method = EXCLUDED.payment_method, payment_details = EXCLUDED.payment_details;",
        (user_id, method, details)
    )
    conn.commit()
    cur.close()
    conn.close()

def get_user_details(user_id):
    """Retrieves user payment details."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT payment_method, payment_details FROM users WHERE user_id = %s;", (user_id,))
    user_data = cur.fetchone()
    cur.close()
    conn.close()
    return user_data

# --- BOT LOGIC ---

# States for ConversationHandler
SELECTING_METHOD, TYPING_DETAILS = range(2)

# Start command and initial setup
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    keyboard = [
        [InlineKeyboardButton("💰 Site Balance (Promo Code)", callback_data='site_balance')],
        [InlineKeyboardButton("💳 Bank Card (Number only)", callback_data='bank_card')],
        [InlineKeyboardButton("🪙 USDT (TRC-20 Address)", callback_data='usdt')],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_html(
        f"Hello, {user.mention_html()}! Welcome to the promo event.\n\n"
        "Please set up your payment method to participate. Your data will be kept secure.",
        reply_markup=reply_markup
    )
    return SELECTING_METHOD

# Handles the user's choice of payment method
async def select_method(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data['payment_method'] = query.data

    prompt_text = ""
    if query.data == 'site_balance':
        prompt_text = "You've selected Site Balance. If your submission is approved, we will provide a promo code for the corresponding amount. Please send any text (e.g., 'ok') to confirm."
    elif query.data == 'bank_card':
        prompt_text = "You've selected Bank Card. Please send your card number (digits only). Do NOT send expiration date or CVV."
    elif query.data == 'usdt':
        prompt_text = "You've selected USDT (TRC-20). Please send your wallet address."

    await query.edit_message_text(text=prompt_text)
    return TYPING_DETAILS

# Handles the user typing their payment details
async def receive_details(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    method = context.user_data.get('payment_method')
    details = "Promo Code" if method == 'site_balance' else update.message.text
    
    # Save details to the database
    save_user_details(user_id, method, details)

    await update.message.reply_text(
        "Thank you! Your payment details have been saved. You can now send me a link to your video.\n\n"
        "Please note: the review process can take a significant amount of time. We appreciate your patience."
    )
    return ConversationHandler.END

# Handles video submissions
async def handle_submission(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    
    # Check if user has set up payment details
    user_details = get_user_details(user.id)
    if not user_details:
        await update.message.reply_text(
            "Please set up your payment details first by using the /start command."
        )
        return

    payment_method, payment_details = user_details
    message_text = update.message.text
    
    admin_message_text = (
        f"New submission from user: {user.mention_html()} (ID: `{user.id}`)\n\n"
        f"Link: {message_text}\n\n"
        f"Chosen Payment Method: `{payment_method}`\n"
        f"Payment Details: `{payment_details}`"
    )

    keyboard = [
        [
            InlineKeyboardButton("✅ Approve", callback_data=f'approve_{user.id}'),
            InlineKeyboardButton("❌ Decline", callback_data=f'decline_{user.id}'),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await context.bot.send_message(
        chat_id=ADMIN_CHAT_ID,
        text=admin_message_text,
        reply_markup=reply_markup,
        parse_mode='HTML'
    )
    
    await update.message.reply_text(
        "Thanks! Your submission has been sent for review.\n\n"
        "Please be aware that due to a high volume of submissions, the review process may be lengthy. Thank you for your patience."
    )

# Handles admin button presses
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    
    action, user_id_str = query.data.split('_')
    user_id = int(user_id_str)
    
    response_text = ""
    if action == 'approve':
        response_text = "✅ Congratulations! Your submission has been APPROVED."
        await query.edit_message_text(text=f"{query.message.text}\n\n--- STATUS: APPROVED ---", parse_mode='HTML')
    else: # decline
        response_text = "❌ Unfortunately, your submission has been DECLINED."
        await query.edit_message_text(text=f"{query.message.text}\n\n--- STATUS: DECLINED ---", parse_mode='HTML')
        
    await context.bot.send_message(chat_id=user_id, text=response_text)

# Main function to run the bot
def main() -> None:
    if not all([TOKEN, ADMIN_CHAT_ID, DATABASE_URL]):
        print("ERROR: Missing one or more environment variables (TOKEN, ADMIN_CHAT_ID, DATABASE_URL).")
        return

    # Set up the database table on startup
    setup_database()
    
    application = Application.builder().token(TOKEN).build()

    # Conversation handler for setting up payment info
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            SELECTING_METHOD: [CallbackQueryHandler(select_method)],
            TYPING_DETAILS: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_details)],
        },
        fallbacks=[CommandHandler('start', start)],
    )

    application.add_handler(conv_handler)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_submission))
    application.add_handler(CallbackQueryHandler(button_handler))

    print("Bot is running...")
    application.run_polling()

if __name__ == "__main__":
    main()

