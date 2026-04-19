import asyncio
import aiohttp
import random
import re
import logging
from datetime import datetime
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# ========== КОНФИГ ==========
BOT_TOKEN = "8709039732:AAGY2cekV_Z3HnQp6fNNBHkPnjGT5xR6LgE"
ADMIN_IDS = [1526536345]
CONCURRENT_TASKS = 100  # Количество одновременных запросов

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Хранилище активных задач
active_tasks = {}

# ========== НАКРУТЧИК ==========
async def fetch_proxies():
    """Собирает бесплатные прокси"""
    proxies = set()
    sources = [
        'https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt',
        'https://raw.githubusercontent.com/jetkai/proxy-list/main/online-proxies/http.txt',
        'https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt'
    ]
    
    async with aiohttp.ClientSession() as session:
        for url in sources:
            try:
                async with session.get(url, timeout=10) as resp:
                    if resp.status == 200:
                        text = await resp.text()
                        found = re.findall(r'\d+\.\d+\.\d+\.\d+:\d+', text)
                        for proxy in found:
                            proxies.add(f'http://{proxy}')
                        logger.info(f"Собрано {len(found)} прокси с {url}")
            except Exception as e:
                logger.error(f"Ошибка сбора {url}: {e}")
    
    return list(proxies)[:500]  # Лимит 500 прокси

async def send_view(session, proxy, url):
    """Отправляет один запрос"""
    headers = {
        'User-Agent': random.choice([
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
        ])
    }
    try:
        async with session.get(url, proxy=proxy, headers=headers, timeout=10) as response:
            return response.status == 200
    except Exception:
        return False

async def booster(post_url, target_views, chat_id, bot, task_id):
    """Основная функция накрутки"""
    # Парсим ссылку
    match = re.search(r't\.me/([^/]+)/(\d+)', post_url)
    if not match:
        await bot.send_message(chat_id, "❌ Неверная ссылка на пост")
        return
    
    channel, post_id = match.group(1), match.group(2)
    full_url = f"https://t.me/{channel}/{post_id}"
    
    await bot.send_message(chat_id, f"🚀 Старт накрутки {target_views} просмотров\n📡 {full_url}\n🔍 Собираю прокси...")
    
    # Собираем прокси
    proxies = await fetch_proxies()
    if not proxies:
        await bot.send_message(chat_id, "❌ Не удалось собрать прокси. Попробуй позже.")
        return
    
    await bot.send_message(chat_id, f"✅ Найдено {len(proxies)} прокси\n⚡ Запускаю...")
    
    # Запускаем накрутку
    success = 0
    failed = 0
    
    async with aiohttp.ClientSession() as session:
        # Берем нужное количество прокси
        proxy_list = proxies[:min(target_views, len(proxies))]
        
        for i, proxy in enumerate(proxy_list):
            if success >= target_views:
                break
            
            # Проверяем, не отменили ли задачу
            if not active_tasks.get(task_id, True):
                await bot.send_message(chat_id, "⏹️ Задача отменена")
                return
            
            if await send_view(session, proxy, full_url):
                success += 1
            else:
                failed += 1
            
            # Отчет каждые 50 просмотров
            if success % 50 == 0:
                await bot.send_message(
                    chat_id, 
                    f"📊 Прогресс: {success}/{target_views}\n✅ Успешно: {success}\n❌ Ошибок: {failed}"
                )
    
    await bot.send_message(
        chat_id,
        f"✅ Готово!\n🎯 {success}/{target_views} просмотров добавлено\n❌ Неудачно: {failed}"
    )
    active_tasks.pop(task_id, None)

# ========== КОМАНДЫ БОТА ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("⛔ У тебя нет доступа к этому боту")
        return
    
    await update.message.reply_text(
        "🤖 *Бот для накрутки просмотров Telegram*\n\n"
        "📌 *Как использовать:*\n"
        "1. Отправь ссылку на пост\n"
        "2. Отправь количество просмотров\n\n"
        "📌 *Пример:*\n"
        "`https://t.me/durov/123`\n"
        "`500`\n\n"
        "⚡ До 5000 просмотров за раз",
        parse_mode='Markdown'
    )
    context.user_data['step'] = 'waiting_link'

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        return
    
    text = update.message.text.strip()
    step = context.user_data.get('step')
    
    if step == 'waiting_link':
        # Проверяем ссылку
        if re.match(r'https?://t\.me/[^/]+/\d+', text):
            context.user_data['post_url'] = text
            context.user_data['step'] = 'waiting_count'
            await update.message.reply_text("📊 Введите количество просмотров (1-5000):")
        else:
            await update.message.reply_text("❌ Отправь правильную ссылку: `https://t.me/канал/123`", parse_mode='Markdown')
    
    elif step == 'waiting_count':
        try:
            count = int(text)
            if 1 <= count <= 5000:
                post_url = context.user_data['post_url']
                context.user_data.clear()
                
                # Создаем задачу
                task_id = str(update.message.message_id)
                active_tasks[task_id] = True
                
                await update.message.reply_text(f"✅ Задача принята!\n🎯 {count} просмотров\n⏳ Запускаю...")
                
                # Запускаем накрутку
                asyncio.create_task(booster(post_url, count, update.effective_chat.id, context.bot, task_id))
            else:
                await update.message.reply_text("❌ Введи число от 1 до 5000")
        except ValueError:
            await update.message.reply_text("❌ Введи число, а не текст")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        return
    
    # Отменяем последнюю задачу
    if active_tasks:
        task_id = list(active_tasks.keys())[-1]
        active_tasks[task_id] = False
        await update.message.reply_text("⏹️ Отменяю последнюю задачу...")
    else:
        await update.message.reply_text("❌ Нет активных задач")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        return
    
    await update.message.reply_text(
        f"📊 *Статус бота*\n\n"
        f"🟢 Активных задач: {len(active_tasks)}\n"
        f"⚡ Потоков: {CONCURRENT_TASKS}\n"
        f"📡 Режим: Накрутка через прокси\n\n"
        f"✅ Бот работает",
        parse_mode='Markdown'
    )

# ========== ЗАПУСК ==========
def main():
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Команды
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    logger.info("🚀 Бот запущен!")
    app.run_polling()

if __name__ == "__main__":
    main()
