import asyncio
import aiohttp
import random
import time
import logging
import re
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

BOT_TOKEN = "8709039732:AAGY2cekV_Z3HnQp6fNNBHkPnjGT5xR6LgE"
ADMIN_IDS = [1526536345]

MAX_CONCURRENT = 10  # Меньше, чтобы не перегружать IP

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class AsyncBooster:
    def __init__(self, post_url, target_views, chat_id, bot):
        self.post_url = post_url
        self.target = target_views
        self.chat_id = chat_id
        self.bot = bot
        parts = post_url.split('/')
        self.channel = parts[3] if len(parts) > 3 else None
        self.post_id = parts[4] if len(parts) > 4 else None
        self.stats = {'success': 0, 'failed': 0}
        self.running = True
        self.semaphore = asyncio.Semaphore(MAX_CONCURRENT)
    
    def _get_ua(self):
        return random.choice([
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
        ])
    
    async def send_view(self, session):
        try:
            # Получаем токен
            async with session.get(
                f'https://t.me/s/{self.channel}',
                timeout=10,
                headers={'User-Agent': self._get_ua()}
            ) as resp:
                if resp.status != 200:
                    return False
                html = await resp.text()
                match = re.search(r'data-view="([^"]+)"', html)
                if not match:
                    return False
                token = match.group(1)
            
            # Отправляем просмотр
            async with session.post(
                f'https://t.me/v/?views={self.post_id}&token={token}',
                timeout=10,
                headers={
                    'User-Agent': self._get_ua(),
                    'Referer': f'https://t.me/{self.channel}/{self.post_id}',
                }
            ) as resp:
                return resp.status == 200
        except Exception as e:
            logger.debug(f"Ошибка: {e}")
            return False
    
    async def worker(self, session):
        while self.running and self.stats['success'] < self.target:
            async with self.semaphore:
                if await self.send_view(session):
                    self.stats['success'] += 1
                    logger.info(f"✅ {self.stats['success']}/{self.target}")
                else:
                    self.stats['failed'] += 1
                    logger.info(f"❌ Ошибка {self.stats['failed']}")
                
                # Ждем 30-60 секунд между просмотрами (чтобы не заблокировали)
                await asyncio.sleep(random.uniform(30, 60))
    
    async def run(self):
        if not self.channel or not self.post_id:
            await self.bot.send_message(chat_id=self.chat_id, text="❌ Неверная ссылка")
            return
        
        await self.bot.send_message(
            chat_id=self.chat_id,
            text=f"🚀 Старт! {self.target} просмотров\n⏳ Без прокси, по 1 просмотру в 30-60 сек"
        )
        
        async with aiohttp.ClientSession() as session:
            # Запускаем 1 воркер (чтобы не перегружать IP)
            worker_task = asyncio.create_task(self.worker(session))
            
            last = 0
            while self.running and self.stats['success'] < self.target:
                await asyncio.sleep(30)
                if self.stats['success'] > last:
                    percent = int(self.stats['success'] / self.target * 100)
                    await self.bot.send_message(
                        chat_id=self.chat_id,
                        text=f"📊 {self.stats['success']}/{self.target} ({percent}%)\n✅ {self.stats['success']} | ❌ {self.stats['failed']}"
                    )
                    last = self.stats['success']
            
            self.running = False
            worker_task.cancel()
        
        await self.bot.send_message(
            chat_id=self.chat_id,
            text=f"✅ Готово!\n🎯 {self.target} | ✅ {self.stats['success']} | ❌ {self.stats['failed']}"
        )

# ========== КОМАНДЫ ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Нет доступа")
        return
    await update.message.reply_text(
        "🤖 Бот для накрутки (без прокси)\n\n"
        "1️⃣ Отправь ссылку\n"
        "2️⃣ Отправь количество\n\n"
        "⚠️ 1 просмотр в 30-60 секунд\n"
        "Пример: https://t.me/durov/123"
    )
    context.user_data['waiting_for_link'] = True

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    text = update.message.text.strip()
    if context.user_data.get('waiting_for_link'):
        if text.startswith('https://t.me/'):
            context.user_data['post_url'] = text
            context.user_data['waiting_for_link'] = False
            context.user_data['waiting_for_count'] = True
            await update.message.reply_text("📊 Введите количество (до 500):")  # Ограничил до 500
        else:
            await update.message.reply_text("❌ Ссылка вида: https://t.me/канал/пост")
    elif context.user_data.get('waiting_for_count'):
        try:
            count = int(text)
            if 1 <= count <= 500:
                post_url = context.user_data.get('post_url')
                context.user_data.clear()
                await update.message.reply_text(f"✅ Задача: {count} просмотров\n⏳ Старт...")
                booster = AsyncBooster(post_url, count, update.effective_chat.id, context.bot)
                asyncio.create_task(booster.run())
            else:
                await update.message.reply_text("❌ Число от 1 до 500 (без прокси медленно)")
        except:
            await update.message.reply_text("❌ Введите число")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ Бот работает (без прокси)\n⏱️ 1 просмотр в 30-60 секунд")

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("🚀 Бот запущен (без прокси)!")
    app.run_polling()

if __name__ == "__main__":
    main()
