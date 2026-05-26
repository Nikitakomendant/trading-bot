# -*- coding: utf-8 -*-

# ---------------------------------------------------------------------------
#                      ОБРАБОТЧИК КОНТЕНТА С ПОМОЩЬЮ ИИ
# ---------------------------------------------------------------------------
# Модуль переведен на модель gemini-2.5-flash, чтобы обойти нулевые суточные
# лимиты (RESOURCE_EXHAUSTED) бесплатного тарифа модели pro.
# ---------------------------------------------------------------------------

import logging
import re
import requests
from PIL import Image
import io
from google import genai
from google.genai.errors import APIError

# Импортируем настройки из нашего конфигурационного файла config.py
from config import (
    GEMINI_API_KEYS,
    TEXT_GENERATION_PROMPT,
    IMAGE_SELECTION_PROMPT
)

# Настройка логирования для этого модуля
logger = logging.getLogger(__name__)

# Проверяем наличие ключей при импорте модуля
if not GEMINI_API_KEYS or len(GEMINI_API_KEYS) == 0:
    logger.critical("Список GEMINI_API_KEYS пуст или не настроен in config.py!")
    raise ValueError("API-ключи для Gemini не предоставлены. Пожалуйста, укажите их в config.py.")


def generate_news_post(article_text: str) -> str | None:
    """
    Генерирует текст новостного поста.
    Использует модель gemini-2.5-flash для обхода ограничений Free Tier.
    """
    logger.info("Начинаю генерацию текста поста с автоматической ротацией ключей...")
    
    # Последовательно перебираем все 5 ключей из конфига
    for i, api_key in enumerate(GEMINI_API_KEYS):
        if not api_key or "ВАШ_" in api_key:
            continue
            
        logger.info(f"Использую Gemini API Ключ №{i+1} (из {len(GEMINI_API_KEYS)})...")
        try:
            # Создаем динамического клиента под конкретный ключ
            client = genai.Client(api_key=api_key)
            
            # Переключено на 'gemini-2.5-flash' для гарантированного обхода лимитов
            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=[TEXT_GENERATION_PROMPT, article_text]
            )
            
            if response and response.text:
                logger.info(f"Текст успешно сгенерирован с помощью Ключа №{i+1}!")
                return response.text
                
        except APIError as e:
            logger.warning(f"⚠️ Ошибка Gemini API с Ключом №{i+1}: {e}. Перехожу к следующему ключу...")
        except Exception as e:
            logger.warning(f"⚠️ Непредвиденная ошибка с Ключом №{i+1}: {e}. Пробую запасной...")

    logger.critical("❌ Ни один из предоставленных API-ключей Gemini в config.py не сработал!")
    return None


def select_best_image(image_urls: list[str], post_text: str) -> str | None:
    """
    Выбирает наиболее релевантное изображение из списка.
    Поддерживает ротацию всех 5 ключей на модели gemini-2.5-flash.
    """
    if not image_urls:
        return None

    logger.info(f"Начинаю анализ {len(image_urls)} изображений с ротацией ключей...")
    
    valid_urls_for_model = []
    index_to_url = {}
    
    # Форматируем промпт, подставляя туда сгенерированный текст поста
    formatted_prompt = IMAGE_SELECTION_PROMPT.format(post_text=post_text)
    prompt_parts = [formatted_prompt, "\nСписок изображений для анализа:\n"]

    # Скачиваем картинки для передачи в модель (ограничиваемся первыми 5 штуками)
    counter = 1
    for url in image_urls[:5]:
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            }
            resp = requests.get(url, headers=headers, timeout=5)
            if resp.status_code == 200:
                img = Image.open(io.BytesIO(resp.content))
                
                prompt_parts.append(f"Зображення {counter}:")
                prompt_parts.append(img)
                
                valid_urls_for_model.append(url)
                index_to_url[counter - 1] = url
                counter += 1
        except Exception as e:
            logger.debug(f"Не удалось загрузить картинку {url} для анализа ИИ: {e}")

    if len(valid_urls_for_model) == 0:
        logger.warning("Не удалось загрузить ни одного изображения для анализа нейросетью.")
        return None

    # Перебираем ключи для анализа картинок
    for i, api_key in enumerate(GEMINI_API_KEYS):
        if not api_key or "ВАШ_" in api_key:
            continue

        try:
            client = genai.Client(api_key=api_key)
            
            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=prompt_parts
            )
            raw = (response.text or "").strip()
            
            m = re.search(r"(\d+)", raw)
            if m:
                idx = int(m.group(1)) - 1
                if 0 <= idx < len(index_to_url):
                    chosen = index_to_url[idx]
                    logger.info(f"ИИ успешно выбрал изображение Ключом №{i+1}: {chosen}")
                    return chosen
                    
            logger.warning(f"ИИ вернул некорректный ответ: '{raw}'. Беру первое валидное изображение.")
            return valid_urls_for_model[0]

        except APIError as e:
            logger.warning(f"⚠️ Ошибка Gemini API при выборе фото с Ключом №{i+1}: {e}. Пробую следующий...")
        except Exception as e:
            logger.warning(f"⚠️ Ошибка с Ключом №{i+1} при выборе фото: {e}. Переключаюсь на запасной...")

    logger.warning("Резервный сценарий: ИИ недоступен на всех ключах. Возвращаю первое изображение по умолчанию.")
    return valid_urls_for_model[0]