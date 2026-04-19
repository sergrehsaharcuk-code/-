import asyncio
import aiohttp
import random
import re
import logging
from datetime import datetime
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

BOT_TOKEN = "8709039732:AAGY2cekV_Z3HnQp6fNNBHkPnjGT5xR6LgE"
ADMIN_IDS = [1526536345]

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Хранилище активных задач
active_tasks = {}

# Глобальный пул прокси
PROXY_POOL = []

async def load_proxies():
    """Загружает прокси из источников"""
    global PROXY_POOL
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
                        logger.info(f"Собрано {len(found)} с {url}")
            except Exception as e:
                logger.error(f"Ошибка {url}: {e}")
    
    PROXY_POOL = list(proxies)
    random.shuffle(PROXY_POOL)
    logger.info(f"📦 Всего прокси в пуле: {len(PROXY_POOL)}")
    return PROXY_POOL

async def send_view(session, proxy, url):
    """Отправляет один запрос через прокси"""
    headers = {
        'User-Agent': random.choice([
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
        ])
    }
    try:
        async with session.get(url, proxy=proxy, headers=headers, timeout=5) as resp:
            return resp.status == 200
    except Exception:
        return False

async def booster(post_url, target_views, chat_id, bot, task_id, status_msg):
    """Накрутка - перебирает прокси пока не накрутит нужное количество"""
    global PROXY_POOL
    
    # Парсим ссылку
    match = re.search(r't\.me/([^/]+)/(\d+)', post_url)
    if not match:
        await status_msg.edit_text("❌ Неверная ссылка на пост")
        return
    
    channel, post_id = match.group(1), match.group(2)
    full_url = f"https://t.me/{channel}/{post_id}"
    
    # Если прокси пустые - загружаем
    if not PROXY_POOL:
        await status_msg.edit_text("🔍 Загружаю прокси...")
        await load_proxies()
        
        if not PROXY_POOL:
            await status_msg.edit_text("❌ Не удалось загрузить прокси")
            return
    
    success = 0
    failed = 0
    proxy_index = 0
    total_proxies = len(PROXY_POOL)
    
    await status_msg.edit_text(f"🚀 Старт: 0/{target_views}\n📡 Перебираю {total_proxies} прокси...")
    
    async with aiohttp.ClientSession() as session:
        # Перебираем прокси по кругу, пока не накрутим нужное количество
        while success < target_views:
            # Проверяем, не остановили ли задачу
            if task_id in active_tasks and not active_tasks[task_id]['active']:
                await status_msg.edit_text(f"⏹️ Остановлено пользователем\n✅ Успешно: {success}\n❌ Ошибок: {failed}")
                return
            
            # Берем следующий прокси (по кругу)
            proxy = PROXY_POOL[proxy_index % total_proxies]
            proxy_index += 1
            
            # Отправляем запрос
            if await send_view(session, proxy, full_url):
                success += 1
                # Обновляем сообщение каждые 10 просмотров
                if success % 10 == 0 or success == target_views:
                    await status_msg.edit_text(
                        f"🚀 Накрутка: {success}/{target_views}\n"
                        f"✅ Успешно: {success}\n"
                        f"❌ Ошибок: {failed}\n"
                        f"🔄 Перебрано прокси: {proxy_index}\n"
                        f"⚡ Статус: РАБОТАЕТ"
                    )
            else:
                failed += 1
            
            # Маленькая задержка, чтобы не спалить
            await asyncio.sleep(0.5)
            
            # Если перебрали все прокси и ничего не накрутили
            if proxy_index > total_proxies * 2 and success == 0:
                await status_msg.edit_text(
                    f"❌ НЕТ ЖИВЫХ ПРОКСИ!\n"
                    f"Перебрано {proxy_index} прокси, 0 успешно.\n"
                    f"Попробуй позже или добавь свои прокси."
                )
                return
    
    # Финальное сообщение
    await status_msg.edit_text(
        f"✅ **ГОТОВО!**\n"
        f"└ {success}/{target_views} просмотров\n"
        f"└ ❌ Ошибок: {failed}\n"
        f"└ 🔄 Перебрано прокси: {proxy_index}\n"
        f"└ 🎯 Эффективность: {int(success/(success+failed)*100) if success+failed > 0 else 0}%",
        parse_mode='Markdown'
    )
    
    # Удаляем задачу из активных
    active_tasks.pop(task_id, None)

# ========== КОМАНДЫ ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Нет доступа")
        return
    
    await update.message.reply_text(
        "🤖 *Бот для накрутки просмотров*\n\n"
        "📌 *Как использовать:*\n"
        "1️⃣ Отправь ссылку на пост\n"
        "2️⃣ Отправь количество просмотров\n\n"
        "🛑 *Команды:*\n"
        "/stop - остановить текущую задачу\n"
        "/status - проверить статус\n\n"
        "⚡ Бот перебирает прокси, пока не накрутит нужное количество",
        parse_mode='Markdown'
    )
    context.user_data['step'] = 'waiting_link'

async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Останавливает текущую активную задачу"""
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Нет доступа")
        return
    
    if not active_tasks:
        await update.message.reply_text("❌ Нет активных задач для остановки")
        return
    
    # Останавливаем все задачи
    for task_id, task_info in active_tasks.items():
        task_info['active'] = False
    
    active_tasks.clear()
    await update.message.reply_text("🛑 Все задачи остановлены")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает статус текущих задач"""
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Нет доступа")
        return
    
    if active_tasks:
        task_list = "\n".join([f"• Задача {tid[:8]}..." for tid in active_tasks.keys()])
        await update.message.reply_text(
            f"🟢 *Активные задачи:* {len(active_tasks)}\n"
            f"{task_list}",
            parse_mode='Markdown'
        )
    else:
        await update.message.reply_text("🟡 Нет активных задач")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    
    text = update.message.text.strip()
    step = context.user_data.get('step')
    
    if step == 'waiting_link':
        if re.match(r'https?://t\.me/[^/]+/\d+', text):
            context.user_data['post_url'] = text
            context.user_data['step'] = 'waiting_count'
            await update.message.reply_text("📊 Введите количество просмотров (1-5000):")
        else:
            await update.message.reply_text("❌ Отправь ссылку: `https://t.me/канал/123`", parse_mode='Markdown')
    
    elif step == 'waiting_count':
        try:
            count = int(text)
            if 1 <= count <= 5000:
                post_url = context.user_data['post_url']
                context.user_data.clear()
                
                # Создаем задачу
                task_id = str(datetime.now().timestamp())
                
                # Отправляем сообщение, которое будет обновляться
                status_msg = await update.message.reply_text(f"🔄 Запускаю накрутку {count} просмотров...")
                
                active_tasks[task_id] = {
                    'active': True,
                    'chat_id': update.effective_chat.id,
                    'message_id': status_msg.message_id
                }
                
                # Запускаем накрутку
                asyncio.create_task(booster(post_url, count, update.effective_chat.id, context.bot, task_id, status_msg))
            else:
                await update.message.reply_text("❌ Введи число от 1 до 5000")
        except ValueError:
            await update.message.reply_text("❌ Введи число, а не текст")

# ========== ЗАПУСК ==========
def main():
    app = Application.builder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stop", stop))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    logger.info("🚀 Бот запущен!")
    logger.info("📌 Команды: /start - начать, /stop - остановить, /status - статус")
    
    # Загружаем прокси через run_coroutine_threadsafe или просто при первом запросе
    # Убираем проблемную строку - прокси загрузятся при первой накрутке
    
    app.run_polling()

if __name__ == "__main__":
    main()
