import json
import os
import uuid
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

PROMO_FILE = "promo_posts.json"

def load_promos():
    """Загружает все рекламные посты из JSON файла."""
    if not os.path.exists(PROMO_FILE):
        return []
    try:
        with open(PROMO_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Ошибка при загрузке рекламных постов: {e}")
        return []

def save_promos(promos):
    """Сохраняет рекламные посты в JSON файл."""
    try:
        with open(PROMO_FILE, "w", encoding="utf-8") as f:
            json.dump(promos, f, ensure_ascii=False, indent=4)
    except Exception as e:
        logger.error(f"Ошибка при сохранении рекламных постов: {e}")

def add_promo(content_html, photo_id, publish_time, publish_date, remaining_count):
    """
    Добавляет новый рекламный пост в базу.
    publish_time: "HH:MM"
    publish_date: "DD.MM.YYYY" или "everyday"
    """
    promos = load_promos()
    new_promo = {
        "id": str(uuid.uuid4())[:8],
        "content_html": content_html,
        "photo_id": photo_id,
        "time": publish_time,
        "date": publish_date,
        "remaining": remaining_count,
        "created_at": datetime.now().isoformat()
    }
    promos.append(new_promo)
    save_promos(promos)
    return new_promo["id"]

def remove_promo(promo_id):
    """Удаляет промо пост по ID."""
    promos = load_promos()
    initial_len = len(promos)
    promos = [p for p in promos if p["id"] != promo_id]
    if len(promos) < initial_len:
        save_promos(promos)
        return True
    return False

def decrement_promo(promo_id):
    """Уменьшает счетчик оставшихся публикаций и удаляет пост, если счетчик = 0."""
    promos = load_promos()
    for p in promos:
        if p["id"] == promo_id:
            p["remaining"] -= 1
            if p["remaining"] <= 0:
                promos.remove(p)
            save_promos(promos)
            return
