# -*- coding: utf-8 -*-

# ---------------------------------------------------------------------------
#                               СБОРЩИК ДАННЫХ
# ---------------------------------------------------------------------------
# Этот модуль отвечает за получение данных из RSS-лент и парсинг
# веб-страниц для извлечения текста статьи и URL-адресов изображений.
# ---------------------------------------------------------------------------

import feedparser
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import os
import re
import logging

# Импортируем настройки из нашего конфигурационного файла
from config import RSS_FEEDS, PUBLISHED_URLS_FILE

# Настройка логирования для этого модуля
logger = logging.getLogger(__name__)


def load_published_urls():
    """Загружает URL уже опубликованных статей из файла."""
    if not os.path.exists(PUBLISHED_URLS_FILE):
        return set()
    try:
        with open(PUBLISHED_URLS_FILE, 'r', encoding='utf-8') as f:
            # Используем set для быстрого поиска
            return set(line.strip() for line in f)
    except Exception as e:
        logger.error(f"Не удалось прочитать файл с опубликованными URL: {e}")
        return set()


def add_url_to_published(url):
    """Добавляет URL в файл опубликованных статей."""
    try:
        with open(PUBLISHED_URLS_FILE, 'a', encoding='utf-8') as f:
            f.write(url + '\n')
    except Exception as e:
        logger.error(f"Не удалось записать URL в файл: {e}")


def get_latest_news_from_rss():
    """
    Сканирует RSS-ленты и возвращает заголовок, URL и краткое описание
    первой неопубликованной новости.

    Returns:
        Кортеж (title, url, summary) или (None, None, None).
    """
    logger.info("Начинаю поиск новых статей в RSS-лентах...")
    published_urls = load_published_urls()

    for feed_url in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            if feed.bozo:
                logger.warning(f"Некорректный формат RSS-ленты: {feed_url}. Ошибка: {feed.bozo_exception}")
                continue

            for entry in feed.entries:
                article_url = entry.link
                if article_url not in published_urls:
                    logger.info(f"Найдена новая статья: '{entry.title}' из {feed_url}")
                    # Сразу добавляем в список, чтобы избежать дублирования при параллельной работе
                    add_url_to_published(article_url)

                    # Извлекаем описание/контент из RSS (fallback для скрапинга)
                    summary = ""
                    if hasattr(entry, 'content') and entry.content:
                        # Некоторые RSS содержат полный контент
                        raw_html = entry.content[0].get('value', '')
                        summary = BeautifulSoup(raw_html, 'html.parser').get_text(separator=' ', strip=True)
                    elif hasattr(entry, 'summary') and entry.summary:
                        summary = BeautifulSoup(entry.summary, 'html.parser').get_text(separator=' ', strip=True)
                    elif hasattr(entry, 'description') and entry.description:
                        summary = BeautifulSoup(entry.description, 'html.parser').get_text(separator=' ', strip=True)

                    summary = re.sub(r'\s+', ' ', summary).strip()
                    if summary:
                        logger.info(f"Из RSS получено описание: {len(summary)} символов.")

                    return entry.title, article_url, summary

        except Exception as e:
            logger.error(f"Ошибка при обработке RSS-ленты {feed_url}: {e}")
            continue

    logger.info("Новых статей не найдено.")
    return None, None, None


def scrape_article_content(url):
    """
    Извлекает сырой текст и все URL изображений со страницы статьи.
    Возвращает словарь {'raw_text': '...', 'image_urls': [...]}.
    """
    logger.info(f"Начинаю скрапинг статьи по URL: {url}")
    try:
        # Устанавливаем заголовок User-Agent, чтобы имитировать браузер
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        }
        response = requests.get(url, headers=headers, timeout=20)
        response.raise_for_status()  # Вызовет исключение для кодов 4xx/5xx

        soup = BeautifulSoup(response.text, "html.parser")

        # --- Извлечение текста ---
        # Пробуем несколько стратегий от более точных к менее точным

        # Стратегия 1: Специфичные селекторы для известных сайтов
        article_body = None
        specific_selectors = [
            ("div", {"class_": re.compile(r'article[-_]?body|post[-_]?body|entry[-_]?content|article[-_]?content|post[-_]?content', re.IGNORECASE)}),
            ("div", {"class_": re.compile(r'article[-_]?text|post[-_]?text|story[-_]?body', re.IGNORECASE)}),
            ("article", {}),
            ("main", {}),
            ("div", {"class_": re.compile(r'post|content|article|text', re.IGNORECASE)}),
        ]

        for tag, attrs in specific_selectors:
            found = soup.find(tag, **attrs) if attrs else soup.find(tag)
            if found:
                # Проверяем, что найденный блок содержит достаточно текста
                test_text = found.get_text(strip=True)
                if len(test_text) > 100:
                    article_body = found
                    logger.debug(f"Найден контейнер статьи: <{tag}> с {len(test_text)} символами.")
                    break

        # Стратегия 2: Извлекаем текст из найденного контейнера
        cleaned_text = ""
        if article_body:
            # Удаляем ненужные теги (скрипты, стили, навигацию и т.д.)
            for tag in article_body(['script', 'style', 'nav', 'header', 'footer', 'aside', 'figure', 'figcaption']):
                tag.decompose()
            raw_text = article_body.get_text(separator=' ', strip=True)
            cleaned_text = re.sub(r'\s+', ' ', raw_text).strip()

        # Стратегия 3 (Fallback): Если текст слишком короткий, собираем все <p> теги
        if len(cleaned_text) < 200:
            logger.info(f"Контейнер статьи дал только {len(cleaned_text)} символов. Пробую fallback через <p> теги...")
            paragraphs = soup.find_all('p')
            p_texts = [p.get_text(strip=True) for p in paragraphs if len(p.get_text(strip=True)) > 40]
            p_combined = ' '.join(p_texts)
            p_combined = re.sub(r'\s+', ' ', p_combined).strip()

            if len(p_combined) > len(cleaned_text):
                cleaned_text = p_combined
                logger.info(f"Fallback через <p> теги дал {len(cleaned_text)} символов.")

        # --- Извлечение изображений ---
        # Ищем изображения во всей странице (не только в article_body),
        # т.к. изображения могут быть вне основного контейнера
        search_scope = article_body if article_body else soup
        image_urls = []
        img_tags = search_scope.find_all('img')

        # Также проверяем <meta property="og:image"> для основного изображения статьи
        og_image = soup.find('meta', property='og:image')
        if og_image and og_image.get('content'):
            og_url = og_image['content']
            if og_url and not og_url.startswith('data:') and len(og_url) > 20:
                image_urls.append(og_url)

        for img in img_tags:
            src = img.get('src') or img.get('data-src') or img.get('data-lazy-src') or ''
            if not src:
                continue
            # Пропускаем data URI и слишком короткие URL (вероятно, пиксели отслеживания)
            if src.startswith('data:') or len(src) < 20:
                continue

            # Фильтруем заведомо неподдерживаемые форматы для Gemini/Telegram (svg, gif)
            lowered = src.lower()
            if lowered.endswith('.svg') or lowered.endswith('.gif'):
                continue

            # Преобразуем относительные URL в абсолютные
            absolute_url = urljoin(url, src)
            image_urls.append(absolute_url)

        # Удаляем дубликаты, сохраняя порядок
        unique_image_urls = list(dict.fromkeys(image_urls))

        logger.info(
            f"Скрапинг успешно завершен. Найдено {len(cleaned_text)} символов текста и {len(unique_image_urls)} изображений.")

        return {
            "raw_text": cleaned_text,
            "image_urls": unique_image_urls
        }

    except requests.exceptions.RequestException as e:
        logger.error(f"Ошибка при загрузке страницы {url}: {e}")
        return None
    except Exception as e:
        logger.error(f"Непредвиденная ошибка при скрапинге {url}: {e}")
        return None