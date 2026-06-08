# -*- coding: utf-8 -*-

# ---------------------------------------------------------------------------
#                      ОБРАБОТЧИК КОНТЕНТА С ПОМОЩЬЮ ИИ
# ---------------------------------------------------------------------------
# Этот модуль взаимодействует с API Groq для:
# 1. Генерации текста новостного поста на основе сырого текста статьи.
# 2. Выбора наиболее релевантного изображения для сгенерированного поста.
#
# Groq API совместим с форматом OpenAI.
# Бесплатные ключи: https://console.groq.com
# ---------------------------------------------------------------------------

from groq import Groq
import logging
import re

# Импортируем настройки из нашего конфигурационного файла
from config import (
    GROQ_API_KEY,
    TEXT_GENERATION_PROMPT,
    IMAGE_SELECTION_PROMPT
)

# Настройка логирования для этого модуля
logger = logging.getLogger(__name__)

# --- Инициализация API ---
if not GROQ_API_KEY or GROQ_API_KEY == "ВАШ_GROQ_API_КЛЮЧ":
    logger.critical("Ключ GROQ_API_KEY не настроен в файле config.py!")
    raise ValueError("API-ключ для Groq не предоставлен. Пожалуйста, укажите его в config.py.")

# Клиент Groq
client = Groq(api_key=GROQ_API_KEY)

# Модель для генерации текста
TEXT_MODEL = "llama-3.3-70b-versatile"


def generate_news_post(article_text: str) -> str | None:
    """
    Генерирует текст поста для Telegram на основе текста статьи с помощью Groq.

    Args:
        article_text: Сырой текст статьи.

    Returns:
        Отформатированный текст поста или None в случае ошибки.
    """
    logger.info(f"Начинаю генерацию текста поста с помощью {TEXT_MODEL} (Groq)...")

    if not article_text or len(article_text.strip()) < 50:
        logger.warning(
            f"Текст статьи слишком короткий ({len(article_text.strip()) if article_text else 0} символов) "
            f"или отсутствует. Пропускаю генерацию."
        )
        return None

    logger.info(f"Длина текста для генерации: {len(article_text.strip())} символов.")

    try:
        prompt = TEXT_GENERATION_PROMPT.format(article_text=article_text)

        response = client.chat.completions.create(
            model=TEXT_MODEL,
            messages=[
                {
                    "role": "user",
                    "content": prompt,
                }
            ],
            temperature=0.7,
            max_tokens=1024,
        )

        generated_post = response.choices[0].message.content

        if not generated_post:
            logger.warning("Модель вернула пустой ответ.")
            return None

        # Очищаем ответ от возможных "оберток" markdown, которые иногда добавляет модель
        cleaned_post = re.sub(r'^```(markdown)?\n|```$', '', generated_post, flags=re.MULTILINE).strip()

        logger.info("Текст поста успешно сгенерирован.")
        return cleaned_post

    except Exception as e:
        logger.error(f"Ошибка при генерации текста поста через Groq API: {e}")
        return None


def select_best_image(image_urls: list, post_text: str) -> str | None:
    """
    Выбирает лучшее изображение из списка URL.
    Возвращается первое доступное изображение.

    Args:
        image_urls: Список URL-адресов изображений.
        post_text: Уже сгенерированный текст поста.

    Returns:
        URL первого изображения или None.
    """
    if not image_urls:
        logger.info("Список URL изображений пуст. Пропускаю выбор изображения.")
        return None

    logger.info(f"Выбираю первое доступное изображение из {len(image_urls)} вариантов.")
    return image_urls[0]