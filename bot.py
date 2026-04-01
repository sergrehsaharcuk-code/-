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

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ========== РЕАЛЬНЫЕ ИСТОЧНИКИ БЕСПЛАТНЫХ ПРОКСИ (ОБНОВЛЯЮТСЯ ЕЖЕДНЕВНО) ==========
PROXY_SOURCES = [
    # Самый надежный источник — прокси проверены за последние 24 часа
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/socks4.txt",
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/socks5.txt",
    
    # Второй надежный источник
    "https://raw.githubusercontent.com/jetkai/proxy-list/main/online-proxies/http.txt",
    "https://raw.githubusercontent.com/jetkai/proxy-list/main/online-proxies/socks5.txt",
    
    # Резерв
    "https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/http.txt",
    "https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/socks5.txt",
]

class ProxyManager:
    def __init__(self):
        self.proxies = []
        self.usage_count = {}
    
    async def collect_proxies(self):
        all_proxies = set()
        async with aiohttp.ClientSession() as session:
            for url in PROXY_SOURCES:
                try:
                    async with session.get(url, timeout=15) as resp:
                        if resp.status == 200:
                            text = await resp.text()
                            proxy_type = 'socks5' if 'socks5' in url else 'http'
                            for line in text.splitlines():
                                line = line.strip()
                                if line and not line.startswith('#'):
                                    if '://' not in line:
                                        line = f"{proxy_type}://{line}"
                                    all_proxies.add(line)
                except Exception as e:
                    logger.debug(f"Ошибка {url}: {e}")
        
        proxies = list(all_proxies)[:500]  # Берем первые 500
        logger.info(f"📦 Собрано {len(proxies)} прокси")
        return proxies
    
    async def update_pool(self):
        self.proxies = await self.collect_proxies()
        for p in self.proxies:
            if p not in self.usage_count:
                self.usage_count[p] = 0
        logger.info(f"🔧 Пул: {len(self.proxies)} прокси")
        return self.proxies
    
    async def get_proxy(self):
        if not self.proxies:
            return None
        proxy = random.choice(self.proxies)
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
        return random.choice([
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
        ])
    
    async def send_view(self, proxy, session):
        try:
            # Получаем токен
            async with session.get(
                f'https://t.me/s/{self.channel}',
                proxy=proxy,
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
                proxy=proxy,
                timeout=10,
                headers={
                    'User-Agent': self._get_ua(),
                    'Referer': f'https://t.me/{self.channel}/{self.post_id}',
                }
            ) as resp:
                return resp.status == 200
        except:
            return False
    
    async def worker(self, session):
        while self.running and self.stats['success'] < self.target:
            proxy = await self.proxy_manager.get_proxy()
            if not proxy:
                await asyncio.sleep(3)
                continue
            
            for _ in range(MAX_VIEWS_PER_PROXY):
                if self.stats['success'] >= self.target or not self.running:
                    break
                
                async with self.semaphore:
                    if await self.send_view(proxy, session):
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
            text=f"🚀 Старт! {self.target} просмотров\n⏳ Собираю прокси..."
        )
        
        await self.proxy_manager.update_pool()
        
        if not self.proxy_manager.proxies:
            await self.bot.send_message(
                chat_id=self.chat_id,
                text="❌ Нет прокси. Попробуйте через 10 минут."
            )
            return
        
        await self.bot.send_message(
            chat_id=self.chat_id,
            text=f"✅ Прокси: {len(self.proxy_manager.proxies)}\n🚀 Начинаю..."
        )
        
        async with aiohttp.ClientSession() as session:
            workers = [asyncio.create_task(self.worker(session)) for _ in range(min(80, len(self.proxy_manager.proxies)))]
            
            last = 0
            while self.running and self.stats['success'] < self.target:
                await asyncio.sleep(10)
                if self.stats['success'] > last:
                    percent = int(self.stats['success'] / self.target * 100)
                    await self.bot.send_message(
                        chat_id=self.chat_id,
                        text=f"📊 {self.stats['success']}/{self.target} ({percent}%)\n✅ {self.stats['success']} | ❌ {self.stats['failed']}\n🔧 Прокси: {len(self.proxy_manager.proxies)}"
                    )
                    last = self.stats['success']
            
            self.running = False
            for w in workers:
                w.cancel()
        
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
        "🤖 Бот для накрутки\n\n1️⃣ Отправь ссылку\n2️⃣ Отправь количество\n\nПример: https://t.me/durov/123"
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
    await update.message.reply_text("✅ Бот работает\n🔧 7 источников прокси\n🔄 Обновление каждый запуск")

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("🚀 Бот запущен!")
    app.run_polling()

if __name__ == "__main__":
    main()
