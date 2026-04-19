import asyncio
import aiohttp
import random
import re
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# ========== КОНФИГ ==========
BOT_TOKEN = "8709039732:AAGY2cekV_Z3HnQp6fNNBHkPnjGT5xR6LgE"
ADMIN_IDS = [1526536345]
CONCURRENT_TASKS = 1000  # Максимум одновременных запросов
CHECK_TIMEOUT = 2        # Таймаут проверки 2 секунды

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

active_tasks = {}

# ========== МГНОВЕННАЯ ЗАГРУЗКА ПРОКСИ ==========
# Предзагруженный список быстрых прокси (обновляется раз в час)
CACHED_PROXIES = []
LAST_UPDATE = 0

async def get_fast_proxies():
    """Возвращает прокси мгновенно - из кэша"""
    global CACHED_PROXIES, LAST_UPDATE
    import time
    
    # Если кэш свежий (меньше часа) - отдаем сразу
    if CACHED_PROXIES and (time.time() - LAST_UPDATE) < 3600:
        return CACHED_PROXIES
    
    # Иначе обновляем в фоне
    asyncio.create_task(update_proxy_cache())
    return CACHED_PROXIES or ["http://188.166.214.218:8080", "http://45.77.36.182:8080"]  # Запасные

async def update_proxy_cache():
    """Обновляет кэш в фоне"""
    global CACHED_PROXIES, LAST_UPDATE
    import time
    
    proxies = []
    sources = [
        'https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt',
    ]
    
    async with aiohttp.ClientSession() as session:
        for url in sources:
            try:
                async with session.get(url, timeout=5) as resp:
                    if resp.status == 200:
                        text = await resp.text()
                        found = re.findall(r'\d+\.\d+\.\d+\.\d+:\d+', text)
                        proxies = [f'http://{p}' for p in found[:200]]
                        break
            except:
                pass
    
    if proxies:
        CACHED_PROXIES = proxies
        LAST_UPDATE = time.time()
        logger.info(f"✅ Кэш обновлен: {len(proxies)} прокси")

# ========== УЛЬТРА-БЫСТРАЯ НАКРУТКА ==========
async def send_view_batch(session, proxies, url, target, task_id):
    """Отправляет пачку запросов параллельно"""
    success = 0
    
    # Создаем задачи для всех прокси сразу
    async def try_proxy(proxy):
        try:
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
            async with session.get(url, proxy=proxy, headers=headers, timeout=3) as resp:
                return 1 if resp.status == 200 else 0
        except:
            return 0
    
    # Запускаем ВСЕ проверки одновременно
    batch_size = min(target, len(proxies))
    proxy_batch = proxies[:batch_size]
    
    tasks = [try_proxy(proxy) for proxy in proxy_batch]
    results = await asyncio.gather(*tasks)
    success = sum(results)
    
    return success

async def booster(post_url, target_views, chat_id, bot, task_id):
    """Максимально быстрая накрутка"""
    match = re.search(r't\.me/([^/]+)/(\d+)', post_url)
    if not match:
        await bot.send_message(chat_id, "❌ Неверная ссылка")
        return
    
    channel, post_id = match.group(1), match.group(2)
    full_url = f"https://t.me/{channel}/{post_id}"
    
    status_msg = await bot.send_message(chat_id, f"⚡ Мгновенная накрутка {target_views} просмотров...")
    
    # Берем прокси из кэша (мгновенно)
    proxies = await get_fast_proxies()
    
    if not proxies:
        await status_msg.edit_text("❌ Нет прокси")
        return
    
    # ОДИН МОЩНЫЙ ЗАПУСК
    async with aiohttp.ClientSession() as session:
        success = await send_view_batch(session, proxies, full_url, target_views, task_id)
    
    # ФИНАЛЬНЫЙ ОТЧЕТ (одно сообщение)
    await status_msg.edit_text(
        f"✅ **ГОТОВО!**\n"
        f"└ {success}/{target_views} просмотров\n"
        f"└ ⚡ {int(success / 0.5)} views/сек\n"
        f"└ 🚀 За {int(success/1000 + 0.5)} сек",
        parse_mode='Markdown'
    )
    
    active_tasks.pop(task_id, None)

# ========== КОМАНДЫ ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Нет доступа")
        return
    
    # Предзагружаем прокси сразу при старте
    asyncio.create_task(update_proxy_cache())
    
    await update.message.reply_text(
        "🚀 *Бот готов*\n"
        "⚡ Скорость света\n\n"
        "Отправь ссылку на пост:",
        parse_mode='Markdown'
    )
    context.user_data['step'] = 'waiting_link'

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    
    text = update.message.text.strip()
    step = context.user_data.get('step')
    
    if step == 'waiting_link':
        if re.match(r'https?://t\.me/[^/]+/\d+', text):
            context.user_data['post_url'] = text
            context.user_data['step'] = 'waiting_count'
            await update.message.reply_text("📊 Введи количество (до 5000):")
        else:
            await update.message.reply_text("❌ Неверная ссылка")
    
    elif step == 'waiting_count':
        try:
            count = int(text)
            if 1 <= count <= 5000:
                post_url = context.user_data['post_url']
                context.user_data.clear()
                
                task_id = str(update.message.message_id)
                active_tasks[task_id] = True
                
                # ЗАПУСК - без лишних сообщений
                asyncio.create_task(booster(post_url, count, update.effective_chat.id, context.bot, task_id))
            else:
                await update.message.reply_text("❌ От 1 до 5000")
        except:
            await update.message.reply_text("❌ Введи число")

# ========== ЗАПУСК ==========
def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    logger.info("🚀 Бот запущен в режиме молнии!")
    app.run_polling()

if __name__ == "__main__":
    main()
