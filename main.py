# -*- coding: utf-8 -*-

import asyncio
import logging
import random
import re
import io
import os
import sys
import requests
from aiohttp import web
from telegram import InputFile
from telegram.ext import Application
from telegram.error import TelegramError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from pytz import timezone

# --- Импорт наших модулей и конфигурации ---
from config import (
    TELEGRAM_TOKEN, CHANNEL_ID, POSTS_PER_DAY,
    TIMEZONE, START_HOUR, END_HOUR, CHANNEL_LINK
)
import data_fetcher
import ai_content_processor
from promo_bot import setup_promo_bot  # Импортируем функцию связки рекламного бота

# --- Настройка системы логирования ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)
logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger('apscheduler').setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

# Инициализируем планировщик
scheduler = AsyncIOScheduler(timezone=timezone(TIMEZONE))


# --- Веб-сервер для обхода засыпания (Render) ---
async def health_check(request):
    return web.Response(text="Bot is awake and running!")

async def start_web_server():
    try:
        app = web.Application()
        app.add_routes([web.get('/', health_check)])
        runner = web.AppRunner(app)
        await runner.setup()
        port = int(os.environ.get("PORT", 8080))
        site = web.TCPSite(runner, '0.0.0.0', port)
        await site.start()
        logger.info(f"Веб-сервер запущен на порту {port} (защита от сна активна)")
    except Exception as e:
        logger.error(f"Ошибка при запуске веб-сервера: {e}")


def convert_to_html_safely(text: str) -> str:
    """Конвертирует базовый Markdown (жирный, курсив) в HTML для Telegram."""
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', text, flags=re.DOTALL)
    text = re.sub(r'__(.*?)__', r'<b>\1</b>', text, flags=re.DOTALL)
    text = re.sub(r'\b_([^_]+)_\b', r'<i>\1</i>', text)
    text = re.sub(r'\*([^\*]+)\*', r'<i>\1</i>', text)
    return text


def split_html_text(text: str, limit: int) -> tuple[str, str]:
    """Разбивает HTML-текст на две части, не разрывая теги, если возможно."""
    if len(text) <= limit:
        return text, ""
    
    split_pos = text.rfind('\n\n', 0, limit)
    if split_pos == -1 or split_pos < limit * 0.7:
        split_pos = text.rfind('\n', 0, limit)
    if split_pos == -1 or split_pos < limit * 0.8:
        split_pos = text.rfind(' ', 0, limit)
    if split_pos == -1:
        split_pos = limit
        
    part1 = text[:split_pos].strip()
    part2 = text[split_pos:].strip()
    
    open_b = part1.count('<b>') - part1.count('</b>')
    open_i = part1.count('<i>') - part1.count('</i>')
    
    if open_i > 0:
        part1 += '</i>'
        part2 = '<i>' + part2
    if open_b > 0:
        part1 += '</b>'
        part2 = '<b>' + part2
        
    return part1, part2


def _build_input_file_from_url(url: str) -> InputFile | None:
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        }
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        content_type = resp.headers.get('Content-Type', '').lower()
        if 'image' not in content_type or 'svg' in content_type:
            return None
        ext = '.jpg'
        if 'png' in content_type:
            ext = '.png'
        elif 'webp' in content_type:
            ext = '.webp'
        elif 'jpeg' in content_type or 'jpg' in content_type:
            ext = '.jpg'
        bio = io.BytesIO(resp.content)
        bio.seek(0)
        return InputFile(bio, filename=f"image{ext}")
    except Exception:
        return None


# Переменная для хранения ссылки на объект бота для планировщика задач
bot_instance = None

async def send_to_telegram(post_text: str, image_url: str | None):
    """Отправляет финальный пост в Telegram-канал."""
    if not bot_instance:
        logger.error("Экземпляр бота не инициализирован.")
        return

    html_text = convert_to_html_safely(post_text)
    link_html = f"\n\n<a href='{CHANNEL_LINK}'>DEVILS TRADERS COMMUNITY</a>"
    
    photo_sent = False
    part2_text = ""

    if image_url:
        caption = html_text
        if len(caption) + len(link_html) > 1024:
            caption, part2_text = split_html_text(html_text, 1024 - len(link_html) - 10) 
            if part2_text:
                logger.info("Текст слишком длинный, разбиваю на 2 поста (фото+текст и только текст).")
            else:
                caption += link_html
        else:
            caption += link_html

        try:
            await bot_instance.send_photo(
                chat_id=CHANNEL_ID,
                photo=image_url,
                caption=caption,
                parse_mode="HTML"
            )
            logger.info("Первая часть поста успешно отправлена с изображением.")
            photo_sent = True
        except TelegramError as e:
            logger.error(f"Ошибка Telegram API при отправке фото по URL: {e}. Пробую загрузить файл.")
            try:
                input_file = _build_input_file_from_url(image_url)
                if input_file:
                    await bot_instance.send_photo(
                        chat_id=CHANNEL_ID,
                        photo=input_file,
                        caption=caption,
                        parse_mode="HTML"
                    )
                    logger.info("Первая часть поста отправлена через загрузку файла.")
                    photo_sent = True
                else:
                    raise TelegramError("Не удалось подготовить файл изображения.")
            except TelegramError as e2:
                logger.error(f"Ошибка при отправке файла изображения: {e2}. Буду отправлять как текст.")

        if photo_sent and part2_text:
            part2_text += link_html
            try:
                await bot_instance.send_message(
                    chat_id=CHANNEL_ID,
                    text=part2_text,
                    parse_mode="HTML",
                    disable_web_page_preview=True
                )
                logger.info("Вторая часть (продолжение текста) успешно отправлена.")
            except TelegramError as e:
                logger.error(f"Ошибка при отправке второй части текста: {e}")
            return

    if not photo_sent:
        full_text = html_text + link_html
        try:
            if len(full_text) > 4096:
                part1, part2 = split_html_text(html_text, 4096 - len(link_html) - 10)
                await bot_instance.send_message(chat_id=CHANNEL_ID, text=part1, parse_mode="HTML", disable_web_page_preview=True)
                part2 += link_html
                await bot_instance.send_message(chat_id=CHANNEL_ID, text=part2, parse_mode="HTML", disable_web_page_preview=True)
                logger.info("Текст без фото был слишком длинным и разделен на два текстовых сообщения.")
            else:
                await bot_instance.send_message(
                    chat_id=CHANNEL_ID,
                    text=full_text,
                    parse_mode="HTML",
                    disable_web_page_preview=True
                )
                logger.info("Пост успешно отправлен как текстовое сообщение.")
        except TelegramError as e:
            logger.error(f"Ошибка Telegram API при отправке текста с HTML: {e}. Пробую без разметки.")
            try:
                plain_text = (post_text + f"\n\nDEVILS TRADERS COMMUNITY: {CHANNEL_LINK}")[:4090]
                await bot_instance.send_message(
                    chat_id=CHANNEL_ID,
                    text=plain_text,
                    parse_mode=None,
                    disable_web_page_preview=True
                )
                logger.info("Пост отправлен как простой текст без разметки.")
            except TelegramError as e2:
                logger.critical(f"Критическая ошибка Telegram API при отправке простого текста: {e2}")


async def process_and_post_news():
    """Основной рабочий цикл: от поиска новости до ее публикации."""
    logger.info("--- Запуск нового цикла обработки новости ---")
    try:
        title, article_url, rss_summary = data_fetcher.get_latest_news_from_rss()
        if not article_url:
            logger.info("Новых статей для публикации не найдено. Цикл завершен.")
            return

        scraped_data = data_fetcher.scrape_article_content(article_url)

        article_text = ""
        if scraped_data and scraped_data.get("raw_text"):
            article_text = scraped_data["raw_text"]

        if len(article_text.strip()) < 200 and rss_summary:
            logger.info(f"Скрапинг дал мало текста. Дополняю из RSS.")
            if article_text.strip():
                article_text = article_text.strip() + " " + rss_summary
            else:
                article_text = rss_summary

        if len(article_text.strip()) < 50 and title:
            logger.warning("Текст статьи слишком короткий. Использую заголовок.")
            article_text = title

        if not article_text or len(article_text.strip()) < 50:
            logger.error(f"Не удалось получить достаточно контента для статьи: {article_url}")
            return

        generated_post = ai_content_processor.generate_news_post(article_text)
        if not generated_post:
            logger.error("Не удалось сгенерировать текст поста. Публикация отменена.")
            return

        best_image_url = None
        if scraped_data and scraped_data.get("image_urls"):
            best_image_url = ai_content_processor.select_best_image(
                image_urls=scraped_data["image_urls"],
                post_text=generated_post
            )

        await send_to_telegram(post_text=generated_post, image_url=best_image_url)

    except Exception as e:
        logger.critical(f"Произошла непредвиденная ошибка в главном цикле: {e}", exc_info=True)


async def on_bot_start(application: Application):
    """
    Эта функция автоматически вызывается библиотека python-telegram-bot СРАЗУ ПОСЛЕ того,
    как инициализируется внутренний цикл событий (event loop).
    Здесь безопасно запускать планировщики и веб-серверы.
    """
    # Запускаем веб-сервер
    await start_web_server()

    # --- Настройка расписания постов через APScheduler ---
    total_minutes_in_range = (END_HOUR - START_HOUR) * 60
    interval_minutes = total_minutes_in_range // POSTS_PER_DAY if POSTS_PER_DAY > 0 else total_minutes_in_range + 1

    for i in range(POSTS_PER_DAY):
        random_offset = random.randint(0, max(0, interval_minutes - 1))
        scheduled_minute_abs = (i * interval_minutes) + random_offset
        scheduled_hour = START_HOUR + scheduled_minute_abs // 60
        scheduled_minute = scheduled_minute_abs % 60

        scheduler.add_job(process_and_post_news, "cron", hour=scheduled_hour, minute=scheduled_minute)
        logger.info(f"Запланирована публикация на {scheduled_hour:02d}:{scheduled_minute:02d}")

    # Теперь ошибки не будет, так как цикл событий уже гарантированно запущен!
    scheduler.start()

    # Запускаем один пост сразу при старте
    asyncio.create_task(process_and_post_news())


def main():
    """Инициализация единого бота и старт опроса."""
    global bot_instance
    logger.info("🤖 Инициализация объединенного Telegram-бота...")

    # Создаем ОДНО общее приложение бота. 
    # Через post_init передаем функцию, которая выполнится внутри рабочего Event Loop.
    application = Application.builder().token(TELEGRAM_TOKEN).post_init(on_bot_start).build()
    bot_instance = application.bot

    # Внедряем команды интерактивного промо-бота в это же приложение
    setup_promo_bot(application)

    # Запуск polling. Бот слушает команды и выполняет фоновые задачи рекламы.
    logger.info("Бот полностью готов и запускает опрос серверов Telegram (Polling)...")
    application.run_polling()


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен вручную.")
    except Exception as e:
        logger.critical(f"Глобальная ошибка при запуске бота: {e}", exc_info=True)