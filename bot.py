import asyncio
import aiohttp
import random
import time
import logging
import re
import os
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

# ========== КОНФИГУРАЦИЯ ==========
BOT_TOKEN = "8709039732:AAGY2cekV_Z3HnQp6fNNBHkPnjGT5xR6LgE"
ADMIN_IDS = [1526536345]

# Настройки накрутки
MAX_VIEWS_PER_PROXY = 5        # Просмотров на один прокси
MAX_CONCURRENT = 150            # Одновременных запросов
PROXY_REFRESH_INTERVAL = 300    # Обновлять прокси каждые 5 минут
MAX_PROXY_CHECK = 500           # Проверять 500 прокси

# ========== ЛОГИРОВАНИЕ ==========
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ========== ЛУЧШИЕ ИСТОЧНИКИ ПРОКСИ ==========
PROXY_SOURCES = [
    "https://raw.githubusercontent.com/jetkai/proxy-list/main/online-proxies/http.txt",
    "https://raw.githubusercontent.com/jetkai/proxy-list/main/online-proxies/socks5.txt",
    "https://raw.githubusercontent.com/prxchk/proxy-list/main/http.txt",
    "https://raw.githubusercontent.com/prxchk/proxy-list/main/socks5.txt",
    "https://raw.githubusercontent.com/roosterkid/openproxylist/main/HTTPS_RAW.txt",
    "https://raw.githubusercontent.com/roosterkid/openproxylist/main/SOCKS5_RAW.txt",
    "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=5000&country=all&ssl=all&anonymity=elite",
    "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=socks5&timeout=5000&country=all",
]

class ProxyManager:
    def __init__(self):
        self.proxies = []
        self.banned = set()
        self.last_update = 0
        self.lock = asyncio.Lock()
        self.usage_count = {}
    
    async def fetch_proxies(self, session, url):
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 200:
                    text = await resp.text()
                    proxies = []
                    for line in text.splitlines():
                        line = line.strip()
                        if line and not line.startswith('#'):
                            if 'socks5' in url:
                                proxy_type = 'socks5'
                            else:
                                proxy_type = 'http'
                            if '://' not in line:
                                line = f"{proxy_type}://{line}"
                            proxies.append(line)
                    return proxies
        except:
            return []
    
    async def collect_proxies(self):
        all_proxies = set()
        async with aiohttp.ClientSession() as session:
            tasks = [self.fetch_proxies(session, url) for url in PROXY_SOURCES]
            results = await asyncio.gather(*tasks)
            for proxies in results:
                for p in proxies:
                    all_proxies.add(p)
        logger.info(f"Собрано {len(all_proxies)} прокси")
        return list(all_proxies)
    
    async def check_proxy(self, proxy):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    'https://t.me',
                    proxy=proxy,
                    timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    return resp.status == 200
        except:
            return False
    
    async def validate_proxies(self, proxies, max_check=500):
        proxies_to_check = proxies[:max_check]
        logger.info(f"Проверяю {len(proxies_to_check)} прокси...")
        valid = []
        checked = 0
        
        for proxy in proxies_to_check:
            if proxy in self.banned:
                continue
            if await self.check_proxy(proxy):
                valid.append(proxy)
            else:
                self.banned.add(proxy)
            checked += 1
            if checked % 100 == 0:
                logger.info(f"Проверено {checked}/{len(proxies_to_check)}. Найдено: {len(valid)}")
        
        logger.info(f"Найдено рабочих: {len(valid)}")
        return valid
    
    async def update_pool(self):
        async with self.lock:
            logger.info("Обновление пула прокси...")
            raw = await self.collect_proxies()
            working = await self.validate_proxies(raw, MAX_PROXY_CHECK)
            for p in working:
                if p not in self.usage_count:
                    self.usage_count[p] = 0
            self.proxies = working
            self.last_update = time.time()
            return self.proxies
    
    async def get_proxy(self):
        if time.time() - self.last_update > PROXY_REFRESH_INTERVAL:
            await self.update_pool()
        async with self.lock:
            if not self.proxies:
                return None
            proxy = min(self.proxies, key=lambda p: self.usage_count.get(p, 0))
            self.usage_count[proxy] = self.usage_count.get(proxy, 0) + 1
            return proxy
    
    async def report_bad(self, proxy):
        async with self.lock:
            if proxy in self.proxies:
                self.proxies.remove(proxy)
                self.banned.add(proxy)

class AsyncBooster:
    def __init__(self, post_url, target_views, chat_id, bot):
        self.post_url = post_url
        self.target = target_views
        self.chat_id = chat_id
        self.bot = bot
        self.channel, self.post_id = self._parse_url(post_url)
        self.stats = {'success': 0, 'failed': 0}
        self.proxy_manager = ProxyManager()
        self.running = True
        self.semaphore = asyncio.Semaphore(MAX_CONCURRENT)
    
    def _parse_url(self, url):
        try:
            parts = url.split('/')
            if len(parts) >= 5:
                return parts[3], parts[4]
        except:
            pass
        return None, None
    
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
                timeout=aiohttp.ClientTimeout(total=8),
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
                    timeout=aiohttp.ClientTimeout(total=8),
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
                await asyncio.sleep(1)
                continue
            
            for _ in range(MAX_VIEWS_PER_PROXY):
                if self.stats['success'] >= self.target:
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
        
        await self.bot.send_message(chat_id=self.chat_id, text=f"🚀 Запускаю накрутку {self.target} просмотров...\n⏳ Собираю прокси (до 1 минуты)...")
        
        await self.proxy_manager.update_pool()
        
        if not self.proxy_manager.proxies:
            await self.bot.send_message(chat_id=self.chat_id, text="❌ Нет рабочих прокси. Попробуйте через 10-20 минут.")
            return
        
        workers = [asyncio.create_task(self.worker()) for _ in range(80)]
        
        last = 0
        while self.running and self.stats['success'] < self.target:
            await asyncio.sleep(10)
            if self.stats['success'] > last:
                percent = int(self.stats['success'] / self.target * 100)
                await self.bot.send_message(
                    chat_id=self.chat_id,
                    text=f"📊 {self.stats['success']}/{self.target} ({percent}%)\n✅ Успешно: {self.stats['success']}\n❌ Ошибок: {self.stats['failed']}\n🔧 Прокси: {len(self.proxy_manager.proxies)}"
                )
                last = self.stats['success']
        
        self.running = False
        for w in workers:
            w.cancel()
        
        await self.bot.send_message(
            chat_id=self.chat_id,
            text=f"✅ Накрутка завершена!\n📌 {self.post_url}\n🎯 {self.target}\n✅ {self.stats['success']}\n❌ {self.stats['failed']}"
        )

# ========== КОМАНДЫ БОТА ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Нет доступа")
        return
    
    await update.message.reply_text(
        "🤖 *Мощный бот для накрутки*\n\n"
        "1. Отправьте ссылку на пост\n"
        "2. Отправьте количество (до 10000)\n\n"
        "Пример: `https://t.me/durov/123`",
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
            await update.message.reply_text("📊 Введите количество просмотров (до 10000):")
        else:
            await update.message.reply_text("❌ Отправьте ссылку вида: https://t.me/канал/пост")
    
    elif context.user_data.get('waiting_for_count'):
        try:
            count = int(text)
            if 1 <= count <= 10000:
                post_url = context.user_data.get('post_url')
                context.user_data.clear()
                
                await update.message.reply_text(f"✅ Задача принята! {count} просмотров\n⏳ Начинаю...")
                
                booster = AsyncBooster(post_url, count, update.effective_chat.id, context.bot)
                asyncio.create_task(booster.run())
            else:
                await update.message.reply_text("❌ Введите число от 1 до 10000")
        except:
            await update.message.reply_text("❌ Введите число")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ Бот работает\n⚡ Асинхронный режим\n🔧 8 источников прокси")

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    logger.info("🚀 Мощный бот запущен!")
    app.run_polling()

if __name__ == "__main__":
    main()
