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

MAX_VIEWS_PER_PROXY = 3   # Уменьшил, чтобы меньше палиться
MAX_CONCURRENT = 50        # Уменьшил, чтобы не перегружать
REQUEST_DELAY = (2, 5)     # Задержка между запросами через один прокси

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ========== ИСТОЧНИКИ ПРОКСИ ==========
PROXY_SOURCES = [
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/socks4.txt",
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/socks5.txt",
    "https://raw.githubusercontent.com/jetkai/proxy-list/main/online-proxies/http.txt",
    "https://raw.githubusercontent.com/jetkai/proxy-list/main/online-proxies/socks5.txt",
]

class ProxyManager:
    def __init__(self):
        self.proxies = []
        self.usage_count = {}
    
    async def fetch_proxies(self, session, url):
        proxies = []
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
                            proxies.append(line)
        except Exception as e:
            logger.debug(f"Ошибка сбора {url}: {e}")
        return proxies
    
    async def collect_proxies(self):
        all_proxies = set()
        async with aiohttp.ClientSession() as session:
            tasks = [self.fetch_proxies(session, url) for url in PROXY_SOURCES]
            results = await asyncio.gather(*tasks)
            for proxies in results:
                for p in proxies:
                    all_proxies.add(p)
        
        proxies = list(all_proxies)[:200]  # Ограничил до 200 для скорости
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
    
    async def report_bad(self, proxy, reason=""):
        if proxy in self.proxies:
            self.proxies.remove(proxy)
            logger.info(f"🗑️ Удален {proxy} ({reason}) | Осталось: {len(self.proxies)}")

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
    
    def _get_headers(self, referer=None):
        headers = {
            'User-Agent': random.choice([
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            ]),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'ru-RU,ru;q=0.8,en-US;q=0.5,en;q=0.3',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        }
        if referer:
            headers['Referer'] = referer
        return headers
    
    async def get_view_token(self, session, proxy):
        """Получает токен просмотра, эмулируя браузер"""
        try:
            # Сначала заходим на страницу поста, получаем куки
            async with session.get(
                f'https://t.me/{self.channel}/{self.post_id}',
                proxy=proxy,
                timeout=15,
                headers=self._get_headers()
            ) as resp:
                if resp.status == 200:
                    html = await resp.text()
                    # Ищем токен в data-view
                    match = re.search(r'data-view="([^"]+)"', html)
                    if match:
                        token = match.group(1)
                        logger.debug(f"Получен токен через {proxy}")
                        return token
                    else:
                        # Если токен не найден, пробуем альтернативный метод
                        match = re.search(r'data-view="([^"]+)"', html)
                        if match:
                            return match.group(1)
                        else:
                            logger.debug(f"Токен не найден для {proxy}")
                            return None
                else:
                    logger.debug(f"Ошибка {resp.status} при получении токена через {proxy}")
                    return None
        except Exception as e:
            logger.debug(f"Ошибка получения токена {proxy}: {e}")
            return None
    
    async def send_view(self, proxy, session):
        """Отправляет просмотр"""
        try:
            token = await self.get_view_token(session, proxy)
            if not token:
                return False
            
            # Отправляем просмотр
            async with session.post(
                f'https://t.me/v/?views={self.post_id}&token={token}',
                proxy=proxy,
                timeout=10,
                headers=self._get_headers(f'https://t.me/{self.channel}/{self.post_id}')
            ) as resp:
                if resp.status == 200:
                    return True
                else:
                    logger.debug(f"Ошибка отправки {resp.status} через {proxy}")
                    return False
        except Exception as e:
            logger.debug(f"Исключение при отправке {proxy}: {e}")
            return False
    
    async def worker(self, session):
        """Воркер"""
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
                        await self.proxy_manager.report_bad(proxy, "неудачная попытка")
                        break
                    
                    await asyncio.sleep(random.uniform(*REQUEST_DELAY))
    
    async def run(self):
        if not self.channel or not self.post_id:
            await self.bot.send_message(chat_id=self.chat_id, text="❌ Неверная ссылка")
            return
        
        await self.bot.send_message(
            chat_id=self.chat_id,
            text=f"🚀 Старт! {self.target} просмотров\n📡 Канал: {self.channel}\n⏳ Собираю прокси..."
        )
        
        await self.proxy_manager.update_pool()
        
        if not self.proxy_manager.proxies:
            await self.bot.send_message(
                chat_id=self.chat_id,
                text="❌ Нет прокси. Попробуйте через 10-20 минут."
            )
            return
        
        await self.bot.send_message(
            chat_id=self.chat_id,
            text=f"✅ Найдено {len(self.proxy_manager.proxies)} прокси\n🚀 Начинаю накрутку..."
        )
        
        async with aiohttp.ClientSession() as session:
            workers = [asyncio.create_task(self.worker(session)) for _ in range(min(40, len(self.proxy_manager.proxies)))]
            
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
            text=f"✅ Готово!\n🎯 {self.target} | ✅ {self.stats['success']} | ❌ {self.stats['failed']}"
        )

# ========== КОМАНДЫ ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Нет доступа")
        return
    await update.message.reply_text(
        "🤖 *Бот для накрутки (публичные каналы)*\n\n"
        "1️⃣ Отправь ссылку на пост\n"
        "2️⃣ Отправь количество (до 5000)\n\n"
        "📌 *Важно:* канал должен быть публичным\n"
        "Пример: `https://t.me/durov/123`",
        parse_mode='Markdown'
    )
    context.user_data['waiting_for_link'] = True

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    text = update.message.text.strip()
    if context.user_data.get('waiting_for_link'):
        if text.startswith('https://t.me/') and '/joinchat' not in text and '/+' not in text:
            context.user_data['post_url'] = text
            context.user_data['waiting_for_link'] = False
            context.user_data['waiting_for_count'] = True
            await update.message.reply_text("📊 Введите количество просмотров (до 5000):")
        else:
            await update.message.reply_text("❌ Отправьте ссылку на *публичный* пост: https://t.me/канал/123", parse_mode='Markdown')
    elif context.user_data.get('waiting_for_count'):
        try:
            count = int(text)
            if 1 <= count <= 5000:
                post_url = context.user_data.get('post_url')
                context.user_data.clear()
                await update.message.reply_text(f"✅ Задача: {count} просмотров\n⏳ Старт...")
                booster = AsyncBooster(post_url, count, update.effective_chat.id, context.bot)
                asyncio.create_task(booster.run())
            else:
                await update.message.reply_text("❌ Число от 1 до 5000")
        except:
            await update.message.reply_text("❌ Введите число")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "✅ Бот работает\n"
        "📢 Для публичных каналов\n"
        "🔧 5 источников прокси\n"
        "⚡ Браузерная эмуляция"
    )

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("🚀 Бот запущен (для публичных каналов)!")
    app.run_polling()

if __name__ == "__main__":
    main()
