import os
import requests
import sqlite3
import logging
import asyncio
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    ConversationHandler,
    filters
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# === ЗАМЕНИ ЭТО НА СВОЙ ТОКЕН ОТ @BotFather ===
BOT_TOKEN = "8420286614:AAEZ6YRwH9lELul5KysgyVTwxXgeP4Hdnxc"

# Состояния диалога
REGION, CATEGORY, PRICE_MIN, PRICE_MAX = range(4)

# === БАЗА ДАННЫХ ===
def init_db():
    conn = sqlite3.connect('kufar_bot.db')
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            chat_id INTEGER PRIMARY KEY,
            region INTEGER,
            category INTEGER,
            price_min INTEGER,
            price_max INTEGER,
            active BOOLEAN DEFAULT 1
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS sent_ads (
            chat_id INTEGER,
            ad_id TEXT,
            PRIMARY KEY (chat_id, ad_id)
        )
    ''')
    conn.commit()
    conn.close()

def save_user_filters(chat_id, region, category, price_min, price_max):
    conn = sqlite3.connect('kufar_bot.db')
    c = conn.cursor()
    c.execute('''
        INSERT OR REPLACE INTO users (chat_id, region, category, price_min, price_max, active)
        VALUES (?, ?, ?, ?, ?, 1)
    ''', (chat_id, region, category, price_min, price_max))
    conn.commit()
    conn.close()

def get_all_active_users():
    conn = sqlite3.connect('kufar_bot.db')
    c = conn.cursor()
    c.execute("SELECT chat_id, region, category, price_min, price_max FROM users WHERE active = 1")
    return c.fetchall()

def is_ad_sent_for_user(chat_id, ad_id):
    conn = sqlite3.connect('kufar_bot.db')
    c = conn.cursor()
    c.execute("SELECT 1 FROM sent_ads WHERE chat_id = ? AND ad_id = ?", (chat_id, ad_id))
    return c.fetchone() is not None

def mark_ad_sent_for_user(chat_id, ad_id):
    conn = sqlite3.connect('kufar_bot.db')
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO sent_ads (chat_id, ad_id) VALUES (?, ?)", (chat_id, ad_id))
    conn.commit()
    conn.close()

# === ЗАПРОС К НОВОМУ GRAPHQL API KUFAR ===
def get_ads_from_kufar(region_id, category_id, price_min=None, price_max=None):
    url = "https://api.kufar.by/graphql"
    
    adverts_query = {
        "category_id": str(category_id),
        "region_id": str(region_id)
    }
    if price_min is not None:
        adverts_query["price_min"] = price_min
    if price_max is not None:
        adverts_query["price_max"] = price_max

    payload = {
        "operationName": "Adverts",
        "variables": {
            "limit": 30,
            "offset": 0,
            "orderBy": "2",  # 2 = сначала новые
            "advertsQuery": adverts_query
        },
        "query": "query Adverts($limit: Int!, $offset: Int!, $orderBy: String!, $advertsQuery: AdvertsQuery!) { advertSearch(limit: $limit, offset: $offset, orderBy: $orderBy, query: $advertsQuery) { adverts { ad_id subject price_byn } total } }"
    }

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        "Content-Type": "application/json"
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()
        return data.get('data', {}).get('advertSearch', {}).get('adverts', [])
    except Exception as e:
        print(f"Ошибка при запросе к Kufar: {e}")
        return []

# === ДИАЛОГ НАСТРОЙКИ ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! 👋 Я отслеживаю новые объявления на Kufar.by.\n\n"
        "Выбери регион (цифра):\n"
        "1 — Брест\n"
        "2 — Минск\n"
        "3 — Гомель\n"
        "4 — Витебск\n"
        "5 — Гродно\n"
        "6 — Могилёв"
    )
    return REGION

async def set_region(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text in ['1', '2', '3', '4', '5', '6']:
        context.user_data['region'] = int(text)
        await update.message.reply_text(
            "Категория:\n"
            "1010 — Авто\n"
            "1140 — Работа\n"
            "1220 — Квартиры\n"
            "1230 — Комнаты\n"
            "1240 — Дома\n"
            "1422 — Электроника"
        )
        return CATEGORY
    else:
        await update.message.reply_text("Пожалуйста, введи цифру от 1 до 6.")
        return REGION

async def set_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    valid_cats = ['1010', '1140', '1220', '1230', '1240', '1422']
    if text in valid_cats:
        context.user_data['category'] = int(text)
        await update.message.reply_text("Минимальная цена (в BYN, например: 200)")
        return PRICE_MIN
    else:
        await update.message.reply_text("Неверный код категории.")
        return CATEGORY

async def set_price_min(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        price = int(update.message.text)
        context.user_data['price_min'] = price
        await update.message.reply_text("Максимальная цена (например: 800)")
        return PRICE_MAX
    except:
        await update.message.reply_text("Пожалуйста, введи число.")
        return PRICE_MIN

async def set_price_max(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        price = int(update.message.text)
        if price > context.user_data.get('price_min', -1):
            context.user_data['price_max'] = price
            chat_id = update.effective_chat.id
            save_user_filters(
                chat_id,
                context.user_data['region'],
                context.user_data['category'],
                context.user_data['price_min'],
                context.user_data['price_max']
            )
            await update.message.reply_text("✅ Готово! Теперь я буду присылать тебе новые объявления по этим фильтрам.")
            return ConversationHandler.END
        else:
            await update.message.reply_text("Макс. цена должна быть больше мин.")
            return PRICE_MAX
    except:
        await update.message.reply_text("Пожалуйста, введи число.")
        return PRICE_MAX

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Настройка отменена. Напиши /start снова.")
    return ConversationHandler.END

# === ПРОВЕРКА ОБЪЯВЛЕНИЙ ДЛЯ ВСЕХ ПОЛЬЗОВАТЕЛЕЙ ===
async def check_and_notify(context: ContextTypes.DEFAULT_TYPE):
    users = get_all_active_users()
    for (chat_id, region, cat, price_min, price_max) in users:
        ads = get_ads_from_kufar(region, cat, price_min, price_max)
        for ad in ads:
            ad_id = str(ad.get('ad_id'))
            if not is_ad_sent_for_user(chat_id, ad_id):
                subject = ad.get('subject', 'Без названия')
                price = ad.get('price_byn', 'Цена не указана')
                url = f"https://kufar.by/item/{ad_id}"
                message = f"🆕 {subject}\n💰 {price} BYN\n🔗 {url}"
                try:
                    await context.bot.send_message(chat_id=chat_id, text=message)
                    mark_ad_sent_for_user(chat_id, ad_id)
                    print(f"Отправлено {ad_id} → {chat_id}")
                except Exception as e:
                    print(f"Ошибка отправки {chat_id}: {e}")

# === ЗАПУСК ===
async def main():
    logging.basicConfig(level=logging.INFO)
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            REGION: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_region)],
            CATEGORY: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_category)],
            PRICE_MIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_price_min)],
            PRICE_MAX: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_price_max)],
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )

    app.add_handler(conv_handler)

    scheduler = AsyncIOScheduler()
    scheduler.add_job(check_and_notify, 'interval', minutes=5, args=[app])
    scheduler.start()

    print("✅ Бот запущен!")
    await app.run_polling()

if __name__ == "__main__":
    asyncio.run(main())
