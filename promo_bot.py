# -*- coding: utf-8 -*-

import logging
import re
from datetime import datetime
import pytz
from telegram import Update
from telegram.ext import (
    CommandHandler, MessageHandler, ConversationHandler, filters, ContextTypes, Application
)

import promo_manager
from config import CHANNEL_ID, TIMEZONE

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

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отменяет текущую операцию добавления рекламы."""
    await update.message.reply_text("❌ Действие отменено.")
    return ConversationHandler.END

async def add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начало процесса добавления рекламы."""
    await update.message.reply_text(
        "📝 Перешли мне или отправь рекламный пост.\n"
        "Это может быть текст или ОДНО фото с подписью. "
        "В посте будет сохранено всё форматирование (жирный, ссылки и т.д.)."
    )
    return WAIT_POST

async def add_receive_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получает пост (текст или фото) и сохраняет его временные данные."""
    if update.message.photo:
        context.user_data['photo_id'] = update.message.photo[-1].file_id
        context.user_data['content_html'] = update.message.caption_html or ""
    elif update.message.text:
        context.user_data['photo_id'] = None
        context.user_data['content_html'] = update.message.text_html
    else:
        await update.message.reply_text("⚠️ Пожалуйста, отправь текст или фото с описанием.")
        return WAIT_POST

    await update.message.reply_text(
        "📅 Введи дату публикации в формате DD.MM.YYYY (например, 25.12.2024)\n"
        "Или напиши слово: everyday (если пост должен выходить каждый день)."
    )
    return WAIT_DATE

async def add_receive_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получает дату публикации."""
    text = update.message.text.strip().lower()
    if text != 'everyday':
        if not re.match(r'^\d{2}\.\d{2}\.\d{4}$', text):
            await update.message.reply_text("⚠️ Неверный формат даты! Используй DD.MM.YYYY или напиши everyday.")
            return WAIT_DATE

    context.user_data['date'] = text
    await update.message.reply_text("⏰ Введи время публикации в формате HH:MM (например, 14:30 или 09:15):")
    return WAIT_TIME

async def add_receive_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получает время публикации."""
    text = update.message.text.strip()
    if not re.match(r'^\d{2}:\d{2}$', text):
        await update.message.reply_text("⚠️ Неверный формат времени! Используй HH:MM (например, 18:00).")
        return WAIT_TIME

    context.user_data['time'] = text
    await update.message.reply_text("🔢 Сколько раз нужно опубликовать этот пост? (Введи число от 1 до 100):")
    return WAIT_COUNT

async def add_receive_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получает количество показов и сохраняет рекламу."""
    text = update.message.text.strip()
    try:
        count = int(text)
        if count <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("⚠️ Введи корректное число больше нуля.")
        return WAIT_COUNT

    # Сохраняем через менеджер
    promo_id = promo_manager.add_promo(
        content_html=context.user_data['content_html'],
        photo_id=context.user_data['photo_id'],
        publish_time=context.user_data['time'],
        publish_date=context.user_data['date'],
        remaining_count=count
    )

    await update.message.reply_text(
        f"✅ Рекламный пост успешно запланирован!\n"
        f"🆔 ID поста: `{promo_id}`\n"
        f"📅 Дата: {context.user_data['date']}\n"
        f"⏰ Время: {context.user_data['time']}\n"
        f"🔄 Количество публикаций: {count}"
    )
    context.user_data.clear()
    return ConversationHandler.END

async def list_promos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Выводит список всех активных рекламных постов."""
    promos = promo_manager.load_promos()
    if not promos:
        await update.message.reply_text("📭 Список запланированной рекламы пуст.")
        return

    response = "📋 **Список запланированной рекламы:**\n\n"
    for p in promos:
        type_str = "📸 Фото+Текст" if p['photo_id'] else "📝 Текст"
        date_str = "Каждый день" if p['date'] == 'everyday' else p['date']
        response += (
            f"🆔 ID: `{p['id']}` | {type_str}\n"
            f"📅 Когда: {date_str} в {p['time']}\n"
            f"🔄 Осталось показов: {p['remaining']}\n"
            f"-----------------------------------\n"
        )
    await update.message.reply_text(response, parse_mode="Markdown")

async def del_promo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Удаляет рекламный пост по ID."""
    if not context.args:
        await update.message.reply_text("⚠️ Использование: /del <ID_поста>")
        return

    promo_id = context.args[0].strip()
    if promo_manager.remove_promo(promo_id):
        await update.message.reply_text(f"🗑 Рекламный пост `{promo_id}` успешно удален.")
    else:
        await update.message.reply_text(f"❌ Пост с ID `{promo_id}` не найден.")

# --- Фоновая задача проверки рекламы ---

async def check_and_publish_promos(context: ContextTypes.DEFAULT_TYPE):
    """Регулярная задача (раз в минуту) для проверки и публикации рекламы."""
    tz = pytz.timezone(TIMEZONE)
    now = datetime.now(tz)
    
    current_time = now.strftime("%H:%M")
    current_date = now.strftime("%d.%m.%Y")

    active_promos = promo_manager.load_promos()
    if not active_promos:
        return

    for p in active_promos:
        if p['remaining'] <= 0:
            continue

        # Проверяем время
        if p['time'] == current_time:
            # Проверяем дату (конкретный день или ежедневно)
            if p['date'] == 'everyday' or p['date'] == current_date:
                logger.info(f"⏰ Наступило время публикации рекламы {p['id']}")
                
                try:
                    if p['photo_id']:
                        await context.bot.send_photo(
                            chat_id=CHANNEL_ID,
                            photo=p['photo_id'],
                            caption=p['content_html'],
                            parse_mode="HTML"
                        )
                    else:
                        await context.bot.send_message(
                            chat_id=CHANNEL_ID,
                            text=p['content_html'],
                            parse_mode="HTML"
                        )
                    
                    # Уменьшаем счетчик показов
                    p['remaining'] -= 1
                    promo_manager.save_promos(active_promos)
                    logger.info(f"Реклама {p['id']} успешно опубликована.")
                except Exception as e:
                    logger.error(f"Ошибка при публикации рекламы {p['id']}: {e}")

# --- Инициализация обработчиков в основном приложении ---

def setup_promo_bot(application: Application):
    """Регистрирует команды промо-бота в едином общем приложении."""
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
    
    # Подключаем фоновое повторение задач проверки рекламы в JobQueue общего бота
    if application.job_queue:
        application.job_queue.run_repeating(check_and_publish_promos, interval=60, first=10)
        
    logger.info("Рекламные обработчики команд успешно внедрены в основного бота.")