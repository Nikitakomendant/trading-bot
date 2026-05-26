import logging
import re
from datetime import datetime
import pytz
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    ConversationHandler, filters, ContextTypes
)

import promo_manager
from config import TELEGRAM_TOKEN, CHANNEL_ID, TIMEZONE

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
# Отключаем спам в консоли от фоновых процессов
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

# Состояния для ConversationHandler
WAIT_POST, WAIT_DATE, WAIT_TIME, WAIT_COUNT = range(4)

# --- Обработчики команд ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отправляет приветственное сообщение и список команд."""
    help_text = (
        "👋 Привет! Я твой бот-ассистент для управления рекламой.\n\n"
        "Доступные команды:\n"
        "🔹 /add - Запланировать новый рекламный пост\n"
        "🔹 /list - Посмотреть список всех запланированных постов\n"
        "🔹 /del <ID> - Удалить пост по его ID\n"
        "🔹 /cancel - Отменить текущее действие (если передумал добавлять)\n\n"
        "Я буду автоматически публиковать рекламу в канал в заданное тобой время!"
    )
    await update.message.reply_text(help_text)


# --- Логика добавления промо (/add) ---

async def add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начинает диалог добавления рекламного поста."""
    await update.message.reply_text(
        "📝 Отправь мне рекламный пост (это может быть текст, или картинка с текстом).\n"
        "Я сохраню его форматирование."
    )
    return WAIT_POST

async def add_receive_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получает рекламный пост от пользователя."""
    message = update.message
    
    content_html = ""
    photo_id = None
    
    if message.photo:
        photo_id = message.photo[-1].file_id # Берем фото наилучшего качества
        content_html = message.caption_html or ""
    else:
        content_html = message.text_html or ""
        
    if not content_html and not photo_id:
        await update.message.reply_text("Пожалуйста, отправь текст или фото.")
        return WAIT_POST
        
    context.user_data['promo_html'] = content_html
    context.user_data['promo_photo'] = photo_id
    
    await update.message.reply_text(
        "✅ Пост получен.\n\n"
        "📅 Напиши дату публикации в формате `ДД.ММ.ГГГГ` (например, `25.05.2026`).\n"
        "Или напиши слово `каждый день`, если пост должен выходить ежедневно.",
        parse_mode="Markdown"
    )
    return WAIT_DATE

async def add_receive_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получает дату публикации."""
    text = update.message.text.strip().lower()
    
    if text in ["каждый день", "everyday", "ежедневно"]:
        context.user_data['promo_date'] = "everyday"
    else:
        # Базовая проверка формата ДД.ММ.ГГГГ
        if not re.match(r"\d{2}\.\d{2}\.\d{4}", text):
            await update.message.reply_text("Неверный формат даты. Пожалуйста, используй формат ДД.ММ.ГГГГ (например, 25.05.2026) или напиши 'каждый день'.")
            return WAIT_DATE
        context.user_data['promo_date'] = text
        
    await update.message.reply_text(
        "⏰ Напиши время публикации по Киеву в формате `ЧЧ:ММ` (например, `14:30`).",
        parse_mode="Markdown"
    )
    return WAIT_TIME

async def add_receive_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получает время публикации."""
    text = update.message.text.strip()
    
    if not re.match(r"^(0[0-9]|1[0-9]|2[0-3]):[0-5][0-9]$", text):
        await update.message.reply_text("Неверный формат времени. Пожалуйста, используй ЧЧ:ММ (например, 14:30 или 09:15).")
        return WAIT_TIME
        
    context.user_data['promo_time'] = text
    
    await update.message.reply_text(
        "🔢 Сколько раз публиковать этот пост?\n"
        "Напиши число (например, `1` если только один раз, или `5` если пять раз подряд по заданному расписанию)."
    )
    return WAIT_COUNT

async def add_receive_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получает количество публикаций и сохраняет рекламу."""
    text = update.message.text.strip()
    
    if not text.isdigit() or int(text) <= 0:
        await update.message.reply_text("Пожалуйста, введи положительное число.")
        return WAIT_COUNT
        
    count = int(text)
    
    # Сохраняем в базу
    promo_id = promo_manager.add_promo(
        content_html=context.user_data['promo_html'],
        photo_id=context.user_data['promo_photo'],
        publish_time=context.user_data['promo_time'],
        publish_date=context.user_data['promo_date'],
        remaining_count=count
    )
    
    date_str = "каждый день" if context.user_data['promo_date'] == "everyday" else context.user_data['promo_date']
    
    await update.message.reply_text(
        f"🎉 **Реклама успешно запланирована!**\n\n"
        f"🆔 ID: `{promo_id}`\n"
        f"📅 День: {date_str}\n"
        f"⏰ Время: {context.user_data['promo_time']}\n"
        f"🔄 Осталось публикаций: {count}",
        parse_mode="Markdown"
    )
    
    # Очищаем временные данные
    context.user_data.clear()
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отменяет процесс создания рекламы."""
    context.user_data.clear()
    await update.message.reply_text("Действие отменено.")
    return ConversationHandler.END


# --- Управление списком реклам (/list, /del) ---

async def list_promos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Выводит список всех запланированных реклам."""
    promos = promo_manager.load_promos()
    
    if not promos:
        await update.message.reply_text("📭 База рекламных постов пуста.")
        return
        
    text = "📋 **Запланированные рекламные посты:**\n\n"
    for i, p in enumerate(promos, 1):
        date_str = "Каждый день" if p['date'] == "everyday" else p['date']
        
        # Делаем превью текста (первые 30 символов) без HTML тегов
        preview = re.sub(r'<[^>]+>', '', p['content_html'])[:30]
        if len(preview) == 30: preview += "..."
        if not preview and p['photo_id']: preview = "[Только картинка]"
        
        text += (
            f"🔹 **ID:** `{p['id']}`\n"
            f"📅 Когда: {date_str} в {p['time']}\n"
            f"🔄 Осталось раз: {p['remaining']}\n"
            f"📝 Текст: {preview}\n"
            f"🖼 Фото: {'Да' if p['photo_id'] else 'Нет'}\n"
            f"🗑 Удалить: `/del {p['id']}`\n\n"
        )
        
    await update.message.reply_text(text, parse_mode="Markdown")


async def del_promo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Удаляет рекламу по ID."""
    if not context.args:
        await update.message.reply_text("Укажи ID поста для удаления. Пример: `/del 1234abcd`", parse_mode="Markdown")
        return
        
    promo_id = context.args[0]
    success = promo_manager.remove_promo(promo_id)
    
    if success:
        await update.message.reply_text(f"✅ Пост с ID `{promo_id}` успешно удален.", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"❌ Пост с ID `{promo_id}` не найден.", parse_mode="Markdown")


# --- Фоновая задача публикации ---

async def check_and_publish_promos(context: ContextTypes.DEFAULT_TYPE):
    """Функция вызывается каждую минуту. Проверяет, пора ли публиковать рекламу."""
    tz = pytz.timezone(TIMEZONE)
    now = datetime.now(tz)
    
    current_time_str = now.strftime("%H:%M")
    current_date_str = now.strftime("%d.%m.%Y")
    
    promos = promo_manager.load_promos()
    
    for p in promos:
        # Проверяем время
        if p["time"] == current_time_str:
            # Проверяем дату
            if p["date"] == "everyday" or p["date"] == current_date_str:
                logger.info(f"Пришло время публиковать рекламу {p['id']}")
                try:
                    # Публикуем
                    if p["photo_id"]:
                        await context.bot.send_photo(
                            chat_id=CHANNEL_ID,
                            photo=p["photo_id"],
                            caption=p["content_html"],
                            parse_mode="HTML"
                        )
                    else:
                        await context.bot.send_message(
                            chat_id=CHANNEL_ID,
                            text=p["content_html"],
                            parse_mode="HTML"
                        )
                    
                    # Уменьшаем счетчик
                    promo_manager.decrement_promo(p["id"])
                    logger.info(f"Реклама {p['id']} успешно опубликована.")
                except Exception as e:
                    logger.error(f"Ошибка при публикации рекламы {p['id']}: {e}")

# --- Главная функция запуска ---

def main():
    logger.info("Запуск интерактивного бота для рекламы...")
    
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    
    # Добавляем ConversationHandler для добавления рекламы
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('add', add_start)],
        states={
            WAIT_POST: [MessageHandler(filters.TEXT | filters.PHOTO, add_receive_post)],
            WAIT_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_receive_date)],
            WAIT_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_receive_time)],
            WAIT_COUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_receive_count)],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("list", list_promos))
    application.add_handler(CommandHandler("del", del_promo))
    application.add_handler(conv_handler)
    
    # Запускаем фоновую задачу раз в минуту
    job_queue = application.job_queue
    job_queue.run_repeating(check_and_publish_promos, interval=60, first=10)
    
    # Запускаем polling (бесконечный цикл получения сообщений)
    application.run_polling()

if __name__ == "__main__":
    main()
