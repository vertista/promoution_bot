import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

# --- ВАЖНО: ДЛЯ ЛОКАЛЬНОГО ТЕСТА ---
# Замените эти строчки на ваши реальные данные.
# Позже мы вернем это на os.getenv() для хостинга.
TOKEN = '8432763718:AAG9ch3nk3W-kGcim_rCkMeFoqXz1kcgnC8' 
ADMIN_CHAT_ID = '5613111142'

# --- ОСНОВНЫЕ ФУНКЦИИ БОТА ---

# Эта функция вызывается, когда пользователь отправляет команду /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    await update.message.reply_html(
        f"Привет, {user.mention_html()}! Присылай мне ссылку на свое видео для участия в акции."
    )

# Эта функция вызывается, когда пользователь отправляет любое текстовое сообщение
async def handle_submission(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message_text = update.message.text
    
    # Создаем текст для сообщения администратору
    admin_message_text = (
        f"Новая заявка от пользователя: {user.mention_html()} (ID: `{user.id}`)\n\n"
        f"Сообщение: {message_text}"
    )

    # Создаем кнопки "Одобрить" и "Отклонить"
    keyboard = [
        [
            InlineKeyboardButton("✅ Одобрить", callback_data=f'approve_{user.id}'),
            InlineKeyboardButton("❌ Отклонить", callback_data=f'decline_{user.id}'),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    # Отправляем сообщение администратору с кнопками
    await context.bot.send_message(
        chat_id=ADMIN_CHAT_ID,
        text=admin_message_text,
        reply_markup=reply_markup,
        parse_mode='HTML'
    )
    
    # Отвечаем пользователю, что его заявка принята
    await update.message.reply_text("Спасибо! Ваша заявка отправлена на проверку.")

# Эта функция вызывается, когда администратор нажимает на одну из кнопок
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    action, user_id_str = query.data.split('_')
    user_id = int(user_id_str)

    if action == 'approve':
        response_text = "🎉 Поздравляем! Ваша заявка была ОДОБРЕНА."
        await query.edit_message_text(
            text=f"{query.message.text}\n\n--- \n✅ ЗАЯВКА ОДОБРЕНА",
            parse_mode='HTML'
        )
    else: # action == 'decline'
        response_text = "😔 К сожалению, ваша заявка была ОТКЛОНЕНА."
        await query.edit_message_text(
            text=f"{query.message.text}\n\n--- \n❌ ЗАЯВКА ОТКЛОНЕНА",
            parse_mode='HTML'
        )

    await context.bot.send_message(chat_id=user_id, text=response_text)

def main() -> None:
    application = Application.builder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_submission))
    application.add_handler(CallbackQueryHandler(button_handler))

    print("Бот запущен...")
    application.run_polling()

if __name__ == "__main__":
    main()