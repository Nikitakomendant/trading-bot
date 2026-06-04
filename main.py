# -*- coding: utf-8 -*-

# ---------------------------------------------------------------------------
#                         ОСНОВНОЙ УПРАВЛЯЮЩИЙ ФАЙЛ
# ---------------------------------------------------------------------------
# Это точка входа в приложение. Он инициализирует все компоненты,
# настраивает планировщик и запускает основной цикл работы бота.
# ---------------------------------------------------------------------------

import asyncio
import logging 
import random
import re
import io
import os
import sys
import subprocess
import requests
from aiohttp import web
from telegram import Bot, InputFile
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

# --- Настройка системы логирования ---
# Логи будут выводиться и в консоль, и в файл (если нужно будет добавить FileHandler)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)
# Приглушаем слишком "болтливые" логгеры от сторонних библиотек
logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger('apscheduler').setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

# --- Инициализация основных компонентов ---
bot = Bot(token=TELEGRAM_TOKEN)
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
    # Экранируем спецсимволы HTML
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    
    # Преобразуем жирный шрифт (сначала двойные звездочки и подчеркивания)
    text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', text, flags=re.DOTALL)
    text = re.sub(r'__(.*?)__', r'<b>\1</b>', text, flags=re.DOTALL)
    
    # Преобразуем курсив (одинарные звездочки и подчеркивания, если они еще остались)
    # Используем word boundaries, чтобы не сломать что-то внутри слов
    text = re.sub(r'\b_([^_]+)_\b', r'<i>\1</i>', text)
    text = re.sub(r'\*([^\*]+)\*', r'<i>\1</i>', text)
    
    return text


def split_html_text(text: str, limit: int) -> tuple[str, str]:
    """Разбивает HTML-текст на две части, не разрывая теги, если возможно."""
    if len(text) <= limit:
        return text, ""
    
    # Ищем лучшее место для разрыва (абзац, конец строки, пробел)
    split_pos = text.rfind('\n\n', 0, limit)
    if split_pos == -1 or split_pos < limit * 0.7:
        split_pos = text.rfind('\n', 0, limit)
    if split_pos == -1 or split_pos < limit * 0.8:
        split_pos = text.rfind(' ', 0, limit)
    if split_pos == -1:
        split_pos = limit
        
    part1 = text[:split_pos].strip()
    part2 = text[split_pos:].strip()
    
    # Балансируем теги, если разрыв произошел внутри <b> или <i>
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


async def send_to_telegram(post_text: str, image_url: str | None):
    """
    Отправляет финальный пост в Telegram-канал.
    Если текст слишком длинный для подписи к фото (1024), он разбивается на два сообщения:
    1. Фото + первая часть текста
    2. Только текст (вторая часть) с ссылкой на канал
    """
    html_text = convert_to_html_safely(post_text)
    link_html = f"\n\n<a href='{CHANNEL_LINK}'>DEVILS TRADERS COMMUNITY</a>"
    
    photo_sent = False
    part2_text = ""

    if image_url:
        caption = html_text
        
        # Если текст слишком длинный для подписи (лимит 1024), разбиваем его
        if len(caption) + len(link_html) > 1024:
            # Разбиваем текст так, чтобы первая часть была <= 1024, а остаток шел во вторую
            caption, part2_text = split_html_text(html_text, 1024 - len(link_html) - 10) 
            # Добавляем ссылку на канал во вторую часть, если она есть
            if part2_text:
                logger.info("Текст слишком длинный, разбиваю на 2 поста (фото+текст и только текст).")
            else:
                caption += link_html
        else:
            caption += link_html

        try:
            await bot.send_photo(
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
                    await bot.send_photo(
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
        except Exception as e:
            logger.error(f"Неизвестная ошибка при отправке фото {image_url}: {e}.")

        # Если фото успешно отправлено, и есть вторая часть текста, отправляем её
        if photo_sent and part2_text:
            part2_text += link_html
            try:
                await bot.send_message(
                    chat_id=CHANNEL_ID,
                    text=part2_text,
                    parse_mode="HTML",
                    disable_web_page_preview=True
                )
                logger.info("Вторая часть (продолжение текста) успешно отправлена.")
            except TelegramError as e:
                logger.error(f"Ошибка при отправке второй части текста: {e}")
            return # Мы закончили, так как отправили и фото, и (если было) продолжение

    # Если отправка с фото не удалась или фото изначально не было (отправляем только текст)
    if not photo_sent:
        full_text = html_text + link_html
        try:
            # Лимит обычного текстового сообщения - 4096 символов
            if len(full_text) > 4096:
                part1, part2 = split_html_text(html_text, 4096 - len(link_html) - 10)
                await bot.send_message(chat_id=CHANNEL_ID, text=part1, parse_mode="HTML", disable_web_page_preview=True)
                part2 += link_html
                await bot.send_message(chat_id=CHANNEL_ID, text=part2, parse_mode="HTML", disable_web_page_preview=True)
                logger.info("Текст без фото был слишком длинным и разделен на два текстовых сообщения.")
            else:
                await bot.send_message(
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
                await bot.send_message(
                    chat_id=CHANNEL_ID,
                    text=plain_text,
                    parse_mode=None,
                    disable_web_page_preview=True
                )
                logger.info("Пост отправлен как простой текст без разметки.")
            except TelegramError as e2:
                logger.critical(f"Критическая ошибка Telegram API при отправке простого текста: {e2}")


async def process_and_post_news():
    """
    Основной рабочий цикл: от поиска новости до ее публикации.
    """
    logger.info("--- Запуск нового цикла обработки новости ---")
    try:
        # 1. Получаем ссылку на последнюю неопубликованную новость (+ описание из RSS)
        title, article_url, rss_summary = data_fetcher.get_latest_news_from_rss()
        if not article_url:
            logger.info("Новых статей для публикации не найдено. Цикл завершен.")
            return

        # 2. Скрапим контент со страницы статьи
        scraped_data = data_fetcher.scrape_article_content(article_url)

        # Определяем текст для генерации: скрапинг → RSS summary → заголовок
        article_text = ""
        if scraped_data and scraped_data.get("raw_text"):
            article_text = scraped_data["raw_text"]

        # Если скрапинг дал мало текста, дополняем из RSS
        if len(article_text.strip()) < 200 and rss_summary:
            logger.info(
                f"Скрапинг дал мало текста ({len(article_text.strip())} символов). "
                f"Дополняю из RSS описания ({len(rss_summary)} символов)."
            )
            # Комбинируем: если есть хоть какой-то текст со скрапинга — добавляем RSS
            if article_text.strip():
                article_text = article_text.strip() + " " + rss_summary
            else:
                article_text = rss_summary

        # Последний fallback: используем заголовок статьи
        if len(article_text.strip()) < 50 and title:
            logger.warning("Текст статьи слишком короткий даже с RSS. Использую заголовок как fallback.")
            article_text = title

        if not article_text or len(article_text.strip()) < 50:
            logger.error(f"Не удалось получить достаточно контента для статьи: {article_url}")
            return

        logger.info(f"Итоговый текст для генерации: {len(article_text.strip())} символов.")

        # 3. Генерируем текст поста с помощью ИИ
        generated_post = ai_content_processor.generate_news_post(article_text)
        if not generated_post:
            logger.error("Не удалось сгенерировать текст поста. Публикация отменена.")
            return

        # 4. Выбираем лучшее изображение с помощью ИИ
        best_image_url = None
        if scraped_data and scraped_data.get("image_urls"):
            best_image_url = ai_content_processor.select_best_image(
                image_urls=scraped_data["image_urls"],
                post_text=generated_post
            )
        else:
            logger.info("В статье не найдено изображений для анализа.")

        # 5. Отправляем готовый пост в Telegram
        await send_to_telegram(post_text=generated_post, image_url=best_image_url)

    except Exception as e:
        logger.critical(f"Произошла непредвиденная ошибка в главном цикле: {e}", exc_info=True)


async def main():
    """
    Главная асинхронная функция, которая настраивает и запускает планировщик.
    """
    logger.info("🤖 Запуск Telegram-бота...")

    # --- Запуск веб-сервера для Render ---
    await start_web_server()

    # --- Настройка расписания ---
    total_minutes_in_range = (END_HOUR - START_HOUR) * 60
    # Предотвращение деления на ноль, если постов 0
    if POSTS_PER_DAY > 0:
        interval_minutes = total_minutes_in_range // POSTS_PER_DAY
    else:
        interval_minutes = total_minutes_in_range + 1

    for i in range(POSTS_PER_DAY):
        # Выбираем случайное время внутри каждого интервала, чтобы посты не выходили в одно и то же время
        random_offset = random.randint(0, max(0, interval_minutes - 1))
        scheduled_minute_abs = (i * interval_minutes) + random_offset

        scheduled_hour = START_HOUR + scheduled_minute_abs // 60
        scheduled_minute = scheduled_minute_abs % 60

        scheduler.add_job(process_and_post_news, "cron", hour=scheduled_hour, minute=scheduled_minute)
        logger.info(f"Запланирована публикация на {scheduled_hour:02d}:{scheduled_minute:02d}")

    scheduler.start()

    # --- Запуск первого поста сразу после старта ---
    logger.info("Запускаю немедленную публикацию первого поста...")
    await process_and_post_news()

    # --- Бесконечный цикл для поддержания работы бота ---
    logger.info("Бот запущен и работает в штатном режиме.")
    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    promo_process = None
    try:
        # Запускаем интерактивного рекламного бота как отдельный фоновый процесс
        logger.info("⚡️ Запуск фонового процесса promo_bot.py...")
        promo_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "promo_bot.py")
        promo_process = subprocess.Popen([sys.executable, promo_script])
        
        # Запускаем основной цикл публикации новостей
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен вручную.")
    except Exception as e:
        logger.critical(f"Глобальная ошибка при запуске бота: {e}", exc_info=True)
    finally:
        # При остановке main.py, завершаем и promo_bot.py
        if promo_process:
            logger.info("🛑 Остановка фонового процесса promo_bot.py...")
            promo_process.terminate()
