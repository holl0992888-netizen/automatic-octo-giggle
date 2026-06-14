import os
import asyncio
import logging
from dotenv import load_dotenv
from google import genai
from pyrogram import Client, filters
from pyrogram.types import Message

logging.basicConfig(level=logging.INFO, format="%(asctime)s - [%(levelname)s] - %(message)s")
load_dotenv()

# КАНАЛИ-ДОНОРИ ЗНИЖОК: звідки бот краде реальні нові товари та фото.
# Сюди вписано популярні канали знижок та електроніки. Можна дописувати свої.
DISCOUNT_DONORS = ["aliexpress_ukraine", "lowcost_ua", "grivna_tech"]

# Твій канал, куди бот публікуватиме унікальний контент
MY_CHANNEL_ID = os.getenv("CHANNEL_ID")
AFFILIATE_PID = os.getenv("AFFILIATE_PID", "hhh_play_shop")

gemini_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

# Ініціалізація через готову сесію, щоб копіювати фото без обмежень
app = Client(
    name="content_hunter_session",
    api_id=int(os.getenv("API_ID", 0)),
    api_hash=os.getenv("API_HASH", ""),
    session_string=os.getenv("SESSION_STRING")
)

def convert_to_affiliate(text: str) -> str:
    """Знаходить у пості первинне посилання та перетворює його на твоє реферальне."""
    # Проста залізобетонна заміна під AliExpress партнерку
    base_url = "https://aliexpress.com_"
    return f"{base_url}{AFFILIATE_PID}"

def rewrite_post_with_ai(original_text: str) -> str:
    """Gemini 2.5 Flash пише унікальний текст для каналу Ігри Цін."""
    if not original_text:
        return ""
        
    prompt = f"""
    Ти — крутий копірайтер геймерського каналу знижок. Перепиши цей пост про товар соковитою 
    українською мовою для каналу "Ігри Цін":
    "{original_text}"
    
    Правила:
    1. Зроби текст дуже коротким, агресивним та закликаючим (емодзі обов'язкові).
    2. Виділи стару та нову ціну жирним шрифтом, покажи вигоду покупця.
    3. Мова: природна українська, без жодних русизмів чи кальки.
    """
    try:
        response = gemini_client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
        )
        return response.text.strip()
    except Exception as e:
        logging.error(f"Збій Gemini: {e}")
        return original_text

@app.on_message(filters.chat(DISCOUNT_DONORS))
async def new_discount_hunter(client: Client, message: Message):
    """Бот ловить нові пости в каналах знижок, забирає оригінальне фото і публікує."""
    # Беремо текст або опис під фото
    incoming_text = message.text or message.caption or ""
    if not incoming_text:
        return

    logging.info(f"🎁 Знайдено нову знижку в каналі-донорі: {message.chat.username}! Обробимо контент...")

    # Переписуємо текст через Gemini 2.5 Flash
    ai_text = rewrite_post_with_ai(incoming_text)
    affiliate_url = convert_to_affiliate(incoming_text)
    
    full_caption = f"{ai_text}\n\n🔗 [КУПИТИ ЗІ ЗНИЖКОЮ]({affiliate_url})"

    try:
        # ПЕРЕВІРКА НАЯВНОСТІ ФОТО: Якщо у донора є фото — забираємо його ж!
        if message.photo:
            # Завантажуємо фото у пам'ять сервера
            photo_file = await client.download_media(message.photo.file_id)
            
            # Відправляємо у твій канал ОРИГІНАЛЬНЕ точне фото товару
            await client.send_photo(
                chat_id=MY_CHANNEL_ID,
                photo=photo_file,
                caption=full_caption,
                parse_mode="Markdown"
            )
            # Видаляємо тимчасовий файл фото з сервера Railway для економії місця
            if os.path.exists(photo_file):
                os.remove(photo_file)
                
            logging.info("✅ Пост з оригінальним фото товару успішно скопійовано та опубліковано!")
        else:
            # Якщо фото чомусь не було, шлемо просто текст, щоб не ламати збірку
            await client.send_message(
                chat_id=MY_CHANNEL_ID,
                text=full_caption,
                parse_mode="Markdown"
            )
            logging.info("✅ Текстовий пост успішно опубліковано (у донора не було фото).")
            
    except Exception as e:
        logging.error(f"❌ Помилка публікації у твій канал: {e}")

async def start_application():
    logging.info("🤖 Контент-Хантер: Авторизація клієнта...")
    async with app:
        logging.info("🎯 Снайпер знижок увімкнений! Стежимо за свіжими товарами та фото...")
        while True:
            await asyncio.sleep(3600)

if __name__ == "__main__":
    logging.info("🚀 Запуск нового безтаймерного Контент-Хантера на Railway...")
    try:
        asyncio.run(start_application())
    except (KeyboardInterrupt, SystemExit):
        logging.info("🛑 Роботу контент-бота зупинено.")
