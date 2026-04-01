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

MAX_VIEWS_PER_PROXY = 5
MAX_CONCURRENT = 150
MAX_PROXY_CHECK = 500
PROXY_REFRESH_INTERVAL = 120

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

PROXY_SOURCES = [
    "https://raw.githubusercontent.com/jetkai/proxy-list/main/online-proxies/http.txt",
    "https://raw.githubusercontent.com/jetkai/proxy-list/main/online-proxies/socks5.txt",
    "https://raw.githubusercontent.com/prxchk/proxy-list/main/http.txt",
    "https://raw.githubusercontent.com/prxchk/proxy-list/main/socks5.txt",
]

class ProxyManager:
    def __init__(self):
        self.proxies = []
        self.usage_count = {}
        self.last_update = 0
        self.updating = False
    
    async def collect_proxies(self):
        all_proxies = set()
        async with aiohttp.ClientSession() as session:
            for url in PROXY_SOURCES:
                try:
                    async with session.get(url, timeout=10) as resp:
                        if resp.status == 200:
                            text = await resp.text()
                            proxy_type = 'socks5' if 'socks5' in url else 'http'
                            for line in text.splitlines():
                                line = line.strip()
                                if line and not line.startswith('#'):
                                    if '://' not in line:
                                        line = f"{proxy_type}://{line}"
                                    all_proxies.add(line)
                except:
                    pass
        proxies = list(all_proxies)[:MAX_PROXY_CHECK]
        logger.info(f"📦 Собрано {len(proxies)} прокси")
        return proxies
    
    async def update_pool(self):
        if self.updating:
            return self.proxies
        
        self.updating = True
        try:
            self.proxies = await self.collect_proxies()
            for p in self.proxies:
                if p not in self.usage_count:
                    self.usage_count[p] = 0
            self.last_update = time.time()
            logger.info(f"🔧 Пул обновлен: {len(self.proxies)} прокси")
        finally:
            self.updating = False
        return self.proxies
    
    async def get_proxy(self):
        if not self.proxies and not self.updating:
            await self.update_pool()
        
        if not self.proxies:
            return None
        
        proxy = min(self.proxies, key=lambda p: self.usage_count.get(p, 0))
        self.usage_count[proxy] = self.usage_count.get(proxy, 0) + 1
        return proxy
    
    async def report_bad(self, proxy):
        if proxy in self.proxies:
            self.proxies.remove(proxy)

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
        self.proxy_manager = ProxyManager()
        self.running = True
        self.semaphore = asyncio.Semaphore(MAX_CONCURRENT)
    
    def _get_ua(self):
        ua = [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
        ]
        return random.choice(ua)
    
    async def get_token(self, session, proxy):
        try:
            async with session.get(
                f'https://t.me/s/{self.channel}',
                proxy=proxy,
                timeout=8,
                headers={'User-Agent': self._get_ua()}
            ) as resp:
                if resp.status == 200:
                    html = await resp.text()
                    match = re.search(r'data-view="([^"]+)"', html)
                    if match:
                        return match.group(1)
        except:
            pass
        return None
    
    async def send_view(self, proxy):
        try:
            async with aiohttp.ClientSession() as session:
                token = await self.get_token(session, proxy)
                if not token:
                    return False
                
                async with session.post(
                    f'https://t.me/v/?views={self.post_id}&token={token}',
                    proxy=proxy,
                    timeout=8,
                    headers={
                        'User-Agent': self._get_ua(),
                        'Referer': f'https://t.me/{self.channel}/{self.post_id}',
                    }
                ) as resp:
                    return resp.status == 200
        except:
            return False
    
    async def worker(self):
        while self.running and self.stats['success'] < self.target:
            proxy = await self.proxy_manager.get_proxy()
            if not proxy:
                await asyncio.sleep(3)
                continue
            
            for _ in range(MAX_VIEWS_PER_PROXY):
                if self.stats['success'] >= self.target or not self.running:
                    break
                
                async with self.semaphore:
                    if await self.send_view(proxy):
                        self.stats['success'] += 1
                        logger.info(f"✅ {self.stats['success']}/{self.target} | {proxy}")
                    else:
                        self.stats['failed'] += 1
                        await self.proxy_manager.report_bad(proxy)
                        break
                    
                    await asyncio.sleep(random.uniform(1, 2))
    
    async def run(self):
        if not self.channel or not self.post_id:
            await self.bot.send_message(chat_id=self.chat_id, text="❌ Неверная ссылка")
            return
        
        await self.bot.send_message(
            chat_id=self.chat_id,
            text=f"🚀 Запускаю накрутку {self.target} просмотров..."
        )
        
        await self.proxy_manager.update_pool()
        
        if not self.proxy_manager.proxies:
            await self.bot.send_message(
                chat_id=self.chat_id,
                text="❌ Не удалось собрать прокси. Попробуйте через 10 минут."
            )
            return
        
        num_workers = min(80, len(self.proxy_manager.proxies))
        workers = [asyncio.create_task(self.worker()) for _ in range(num_workers)]
        logger.info(f"🚀 Запущено {num_workers} воркеров, прокси: {len(self.proxy_manager.proxies)}")
        
        last_success = 0
        while self.running and self.stats['success'] < self.target:
            await asyncio.sleep(10)
            if self.stats['success'] > last_success:
                percent = int(self.stats['success'] / self.target * 100)
                await self.bot.send_message(
                    chat_id=self.chat_id,
                    text=f"📊 {self.stats['success']}/{self.target} ({percent}%)\n"
                         f"✅ {self.stats['success']} | ❌ {self.stats['failed']}\n"
                         f"🔧 Прокси: {len(self.proxy_manager.proxies)}"
                )
                last_success = self.stats['success']
        
        self.running = False
        for w in workers:
            w.cancel()
        
        await self.bot.send_message(
            chat_id=self.chat_id,
            text=f"✅ Накрутка завершена!\n"
                 f"📌 {self.post_url}\n"
                 f"🎯 {self.target} | ✅ {self.stats['success']} | ❌ {self.stats['failed']}"
        )

# ========== КОМАНДЫ БОТА ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Нет доступа")
        return
    
    await update.message.reply_text(
        "🤖 *Бот для накрутки*\n\n"
        "1️⃣ Отправьте ссылку на пост\n"
        "2️⃣ Отправьте количество (до 10000)\n\n"
        "📝 *Пример:* `https://t.me/durov/123`",
        parse_mode='Markdown'
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
            await update.message.reply_text("📊 Введите количество (до 10000):")
        else:
            await update.message.reply_text("❌ Ссылка вида: https://t.me/канал/пост")
    
    elif context.user_data.get('waiting_for_count'):
        try:
            count = int(text)
            if 1 <= count <= 10000:
                post_url = context.user_data.get('post_url')
                context.user_data.clear()
                
                await update.message.reply_text(f"✅ Задача: {count} просмотров\n⏳ Старт...")
                
                booster = AsyncBooster(post_url, count, update.effective_chat.id, context.bot)
                asyncio.create_task(booster.run())
            else:
                await update.message.reply_text("❌ Число от 1 до 10000")
        except:
            await update.message.reply_text("❌ Введите число")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ Бот работает\n⚡ Асинхронный режим")

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    logger.info("🚀 Бот запущен!")
    app.run_polling()

if __name__ == "__main__":
    main()
