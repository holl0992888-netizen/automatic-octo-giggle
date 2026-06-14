import os
import asyncio
import logging
import re
from datetime import datetime, timedelta
import aiohttp
import feedparser
from bs4 import BeautifulSoup
from dotenv import load_dotenv

# Драйвери Telegram Bot API та Планувальника
from aiogram import Bot, Dispatcher
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.storage.memory import MemoryStorage
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# Офіційний сучасний SDK від Google
from google import genai
from google.genai import types

# Налаштування логування для Railway (вивід у stdout/stderr)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("AliExpressBot")

load_dotenv()

# Ініціалізація конфігурації
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
AFFILIATE_PID = os.getenv("AFFILIATE_PID")

# RSS стрічка гарячих товарів AliExpress
ALIEXPRESS_RSS_URL = "https://aliexpress.com" 

# Валідація критичних налаштувань під час старту
if not all([BOT_TOKEN, CHANNEL_ID, GEMINI_API_KEY, AFFILIATE_PID]):
    logger.critical("❌ Відсутні необхідні змінні оточення! Перевірте .env або налаштування Railway.")
    exit(1)

# Ініціалізація клієнтів
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
ai_client = genai.Client(api_key=GEMINI_API_KEY)
scheduler = AsyncIOScheduler()

async def check_is_duplicate(target_url: str) -> bool:
    """
    Асинхронно перевіряє останні 50 повідомлень у каналі.
    Шукає збіги за текстом або за URL інлайн-кнопок, щоб виключити дублікати.
    """
    try:
        logger.info(f"Перевірка лінка на дублікати в каналі {CHANNEL_ID}...")
        # Очищаємо вхідне посилання для точного зіставлення ID товару
        cleaned_target = target_url.split('?')[0].strip()
        
        history = await bot.get_chat_history(chat_id=CHANNEL_ID, limit=50)
        for message in history:
            # 1. Пошук за текстом повідомлення
            if message.text and cleaned_target in message.text:
                return True
            if message.caption and cleaned_target in message.caption:
                return True
                
            # 2. Пошук за URL-адресами кнопок під повідомленням
            if message.reply_markup and message.reply_markup.inline_keyboard:
                for row in message.reply_markup.inline_keyboard:
                    for button in row:
                        if button.url and cleaned_target in button.url:
                            return True
    except Exception as e:
        logger.error(f"⚠️ Помилка під час перевірки історії каналу (get_chat_history): {e}")
        # У разі збою API вважаємо, що дублікат є, заради безпеки контенту
        return True
    return False

async def generate_marketing_post(product_title: str) -> str:
    """
    Генерація рекламного тексту через Gemini 2.5 Flash виключно українською мовою.
    """
    prompt = f"""
    Ти — професійний копірайтер. Напиши короткий, агресивний, маркетинговий пост для Telegram-каналу на основі назви товару: "{product_title}".
    
    СУВОРІ ПРАВИЛА:
    1. Текс має бути написаний виключно чистою українською мовою. Ніяких русизмів, кальок чи суржику (забудь слова "заказ", "скидка", "доставка безкоштовна" замість "безкоштовна доставка").
    2. Додай яскраві емодзі, що привертають увагу та чіпляють погляд.
    3. Штучно та реалістично підкресли велику знижку (наприклад, -50% або -70%).
    4. Обов'язково виділи цінові блоки жирним шрифтом (Markdown), використовуючи вигадану, але реалістичну ціну в гривнях (наприклад, **🔥 Стара ціна: 1200 грн | Нова ціна: 599 грн**).
    5. Заверши пост чітким та сильним закликом до дії (CTA) купити товар.
    6. Текст має бути лаконічним, без «води», розрахованим на швидке читання в стрічці новин.
    """
    try:
        response = ai_client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
        )
        if response.text:
            return response.text.strip()
    except Exception as e:
        logger.error(f"⚠️ Помилка інтеграції з Gemini API: {e}")
    
    # Резервний шаблон на випадок збою AI
    return f"🔥 **ШОК-ЦІНА на AliExpress!** 🔥\n\n🛍 {product_title}\n\n📉 **Знижка -50% просто зараз!**\n\nПоспішай забрати крутий девайс за найкращою ціною в Україні! Кількість обмежена!"

async def fetch_and_post_deal():
    """
    Основна бізнес-логіка: парсинг RSS, фільтрація дублів, AI-генерація та публікація в канал.
    """
    logger.info("Запуск процесу парсингу свіжих пропозицій...")
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(ALIEXPRESS_RSS_URL, timeout=15) as response:
                if response.status != 200:
                    logger.error(f"Не вдалося отримати RSS-стрічку. Статус: {response.status}")
                    return
                xml_data = await response.text()
        except Exception as e:
            logger.error(f"❌ Помилка мережі під час запиту до RSS: {e}")
            return

    # Парсимо XML структуру RSS-фіду
    feed = feedparser.parse(xml_data)
    if not feed.entries:
        logger.warning("RSS стрічка виявилася порожньою.")
        return

    for entry in feed.entries:
        try:
            original_url = entry.link
            title = entry.title
            description = entry.get("description", "")

            # Вилучаємо точний URL зображення (img src) з опису товару
            img_src = None
            if description:
                soup = BeautifulSoup(description, "html.parser")
                img_tag = soup.find("img")
                if img_tag and img_tag.get("src"):
                    img_src = img_tag["src"]

            if not img_src:
                logger.info(f"Пропуск товару '{title}': відсутнє валідне зображення.")
                continue

            # Перевірка на дублікат у Telegram-каналі
            if await check_is_duplicate(original_url):
                logger.info(f"Пропуск товару (вже публікувався): {title}")
                continue

            # Формування партнерського посилання (Admitad wrapper формат)
            cleaned_url = original_url.split('?')[0].strip()
            affiliate_url = f"https://aliexpress.com_{AFFILIATE_PID}?target={cleaned_url}"

            # Генерація контенту за допомогою штучного інтелекту
            post_text = await generate_marketing_post(title)

            # Створення інлайн-клавіатури з кнопкою переходу
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🛒 КУПИТИ ЗІ ЗНИЖКОЮ", url=affiliate_url)]
            ])

            # Публікація в канал (фото + опис + кнопка)
            # Окремий блок try/except гарантує, що бите посилання на картинку не зламає цикл
            try:
                await bot.send_photo(
                    chat_id=CHANNEL_ID,
                    photo=img_src,
                    caption=post_text,
                    parse_mode="Markdown",
                    reply_markup=keyboard
                )
                logger.info(f"✅ Успішно опубліковано новий пост: {title}")
                break # Виходимо з циклу стрічки одразу після першого успішного унікального поста
            except Exception as photo_err:
                logger.error(f"⚠️ Помилка відправки фото ({img_src}) для товару '{title}': {photo_err}")
                continue # Якщо картинка невалідна, пробуємо наступний офер із фіду

        except Exception as item_err:
            logger.error(f"⚠️ Помилка обробки елемента фіду: {item_err}")
            continue

async def main():
    logger.info("🚀 Запуск автономного Telegram Content Bot...")
    
    # 1. Миттєвий тест-тригер при старті сервера для перевірки працездатності
    logger.info("⚡ Виконання моментального стартового тесту (1-й пост)...")
    await fetch_and_post_deal()

    # 2. Планування 2-го поста рівно через 5 хвилин після старту
    scheduler.add_job(
        fetch_and_post_deal,
        'date',
        run_date=datetime.now() + timedelta(minutes=5),
        id='second_post_job'
    )
    logger.info("📅 Друга публікація успішно запланована на +5 хвилин від поточного часу.")

    # 3. Планирування всіх наступних постів строго кожні 2 години (7200 секунд)
    scheduler.add_job(
        fetch_and_post_deal,
        'interval',
        hours=2,
        id='main_loop_job'
    )
    logger.info("📅 Регулярний інтервальний цикл (кожні 2 години) активовано.")

    # Старт планувальника задач
    scheduler.start()

    # Утримання процесу активним (24/7 worker сесія на Railway)
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот зупинений.")
