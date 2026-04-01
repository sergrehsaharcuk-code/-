import requests
import random
import time
import threading
import re
import logging
import os
from queue import Queue
from urllib.parse import urlparse
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters, ConversationHandler

# ========== КОНФИГУРАЦИЯ ==========
BOT_TOKEN = "8709039732:AAGY2cekV_Z3HnQp6fNNBHkPnjGT5xR6LgE"
ADMIN_IDS = [1526536345]  # Ваш ID

# Настройки накрутки
MAX_VIEWS_PER_PROXY = 5        # 5 просмотров на один IP
THREADS = 20                    # Количество потоков
DELAY_BETWEEN_VIEWS = (2, 5)    # Задержка между просмотрами (сек)
PROXY_REFRESH_INTERVAL = 600    # Обновлять прокси каждые 10 минут
MAX_PROXY_CHECK = 100           # Проверять только первые 100 прокси

# Состояния для ConversationHandler
WAITING_FOR_LINK, WAITING_FOR_COUNT = range(2)

# ========== ЛОГИРОВАНИЕ ==========
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ========== ПРОКСИ МЕНЕДЖЕР ==========
# Только 3 самых надежных источника (быстрая загрузка)
PROXY_SOURCES = [
    ("https://raw.githubusercontent.com/jetkai/proxy-list/main/online-proxies/http.txt", "http"),
    ("https://raw.githubusercontent.com/jetkai/proxy-list/main/online-proxies/socks5.txt", "socks5"),
    ("https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=5000&country=all&ssl=all&anonymity=elite", "http"),
]

class ProxyManager:
    def __init__(self):
        self.proxies = []
        self.lock = threading.Lock()
        self.last_update = 0
        self.banned_proxies = set()
        self.proxy_usage_count = {}
    
    def _get_ua(self):
        ua_list = [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
            'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36',
        ]
        return random.choice(ua_list)
    
    def fetch_from_source(self, url, proxy_type):
        proxies = []
        try:
            response = requests.get(url, timeout=10, headers={'User-Agent': self._get_ua()})
            if response.status_code == 200:
                for line in response.text.splitlines():
                    line = line.strip()
                    if line and not line.startswith('#'):
                        if '://' not in line:
                            line = f"{proxy_type}://{line}"
                        proxies.append(line)
            return proxies
        except Exception as e:
            logger.debug(f"Ошибка при сборе с {url}: {e}")
            return []
    
    def collect_proxies(self):
        all_proxies = set()
        logger.info(f"Начинаю сбор прокси из {len(PROXY_SOURCES)} источников...")
        
        for url, proxy_type in PROXY_SOURCES:
            proxies = self.fetch_from_source(url, proxy_type)
            for p in proxies:
                all_proxies.add(p)
            time.sleep(0.3)  # Маленькая задержка между источниками
        
        logger.info(f"Всего собрано уникальных прокси: {len(all_proxies)}")
        return list(all_proxies)
    
    def check_proxy(self, proxy):
        try:
            proxies = {'http': proxy, 'https': proxy}
            response = requests.get(
                'https://t.me',
                proxies=proxies,
                timeout=5,  # Быстрая проверка
                headers={'User-Agent': self._get_ua()}
            )
            return response.status_code == 200
        except:
            return False
    
    def validate_proxies(self, proxies, max_check=100):
        """Проверяет только первые max_check прокси"""
        if not proxies:
            return []
        
        # Берем только первые max_check прокси
        proxies_to_check = proxies[:max_check]
        logger.info(f"Проверяем {len(proxies_to_check)} прокси (из {len(proxies)} всего)...")
        
        valid = []
        checked = 0
        
        for proxy in proxies_to_check:
            if proxy in self.banned_proxies:
                continue
            
            checked += 1
            if checked % 20 == 0:
                logger.info(f"Проверено {checked}/{len(proxies_to_check)} прокси. Найдено: {len(valid)}")
            
            if self.check_proxy(proxy):
                valid.append(proxy)
            else:
                self.banned_proxies.add(proxy)
        
        logger.info(f"Найдено рабочих прокси: {len(valid)}")
        return valid
    
    def update_pool(self):
        with self.lock:
            logger.info("Обновление пула прокси...")
            raw_proxies = self.collect_proxies()
            working_proxies = self.validate_proxies(raw_proxies, MAX_PROXY_CHECK)
            
            for proxy in working_proxies:
                if proxy not in self.proxy_usage_count:
                    self.proxy_usage_count[proxy] = 0
            
            self.proxies = working_proxies
            self.last_update = time.time()
            logger.info(f"Пул прокси обновлен: {len(self.proxies)} рабочих прокси")
            return self.proxies
    
    def get_proxy(self):
        if time.time() - self.last_update > PROXY_REFRESH_INTERVAL:
            self.update_pool()
        
        with self.lock:
            if not self.proxies:
                return None
            # Выбираем прокси с наименьшим количеством использований
            proxy = min(self.proxies, key=lambda p: self.proxy_usage_count.get(p, 0))
            self.proxy_usage_count[proxy] = self.proxy_usage_count.get(proxy, 0) + 1
            return proxy
    
    def report_bad_proxy(self, proxy):
        with self.lock:
            if proxy in self.proxies:
                self.proxies.remove(proxy)
                self.banned_proxies.add(proxy)
                logger.debug(f"Прокси {proxy} удален из пула")

# ========== НАКРУТЧИК ==========
class ViewBooster:
    def __init__(self, post_url, target_views, chat_id, bot):
        self.post_url = post_url
        self.target_views = target_views
        self.chat_id = chat_id
        self.bot = bot
        self.channel, self.post_id = self._parse_url(post_url)
        self.stats = {'success': 0, 'failed': 0}
        self.stop_event = threading.Event()
        self.proxy_manager = ProxyManager()
        self.running = True
    
    def _parse_url(self, post_url):
        try:
            path = urlparse(post_url).path
            parts = path.strip('/').split('/')
            if len(parts) >= 2:
                return parts[0], parts[1]
        except:
            pass
        return None, None
    
    def _get_ua(self):
        ua_list = [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
            'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36',
        ]
        return random.choice(ua_list)
    
    def _get_view_token(self, proxy):
        try:
            proxies = {'http': proxy, 'https': proxy}
            resp = requests.get(
                f'https://t.me/s/{self.channel}',
                proxies=proxies,
                timeout=10,
                headers={'User-Agent': self._get_ua()}
            )
            if resp.status_code == 200:
                match = re.search(r'data-view="([^"]+)"', resp.text)
                if match:
                    return match.group(1)
        except:
            pass
        return None
    
    def _send_view(self, proxy):
        try:
            token = self._get_view_token(proxy)
            if not token:
                return False
            
            proxies = {'http': proxy, 'https': proxy}
            view_url = f"https://t.me/v/?views={self.post_id}&token={token}"
            
            resp = requests.post(
                view_url,
                proxies=proxies,
                timeout=10,
                headers={
                    'User-Agent': self._get_ua(),
                    'Accept': '*/*',
                    'Referer': f'https://t.me/{self.channel}/{self.post_id}',
                }
            )
            return resp.status_code == 200
        except:
            return False
    
    def _worker(self):
        """Поток-воркер: берет прокси, делает до MAX_VIEWS_PER_PROXY просмотров"""
        while not self.stop_event.is_set() and self.stats['success'] < self.target_views:
            proxy = self.proxy_manager.get_proxy()
            if not proxy:
                time.sleep(2)
                continue
            
            views_done = 0
            for _ in range(MAX_VIEWS_PER_PROXY):
                if self.stats['success'] >= self.target_views or self.stop_event.is_set():
                    break
                
                if self._send_view(proxy):
                    self.stats['success'] += 1
                    views_done += 1
                    logger.info(f"✅ Просмотр #{self.stats['success']} | Прокси: {proxy}")
                else:
                    self.stats['failed'] += 1
                    self.proxy_manager.report_bad_proxy(proxy)
                    break
                
                time.sleep(random.uniform(*DELAY_BETWEEN_VIEWS))
    
    def run(self):
        """Запускает накрутку"""
        if not self.channel or not self.post_id:
            return "❌ Неверная ссылка на пост"
        
        # Отправляем сообщение о старте
        try:
            self.bot.send_message(
                chat_id=self.chat_id,
                text=f"🚀 Запускаю накрутку...\n\n📌 Пост: {self.post_url}\n🎯 Цель: {self.target_views} просмотров\n\n⏳ Собираю прокси (1-2 минуты)..."
            )
        except:
            pass
        
        # Обновляем пул прокси
        self.proxy_manager.update_pool()
        
        if not self.proxy_manager.proxies:
            return "❌ Не найдено рабочих прокси. Попробуйте позже (через 10-20 минут)."
        
        # Запускаем потоки
        threads = []
        num_threads = min(THREADS, len(self.proxy_manager.proxies))
        for i in range(num_threads):
            t = threading.Thread(target=self._worker, name=f"Worker-{i+1}")
            t.daemon = True
            t.start()
            threads.append(t)
        
        logger.info(f"Запущено {len(threads)} потоков, прокси: {len(self.proxy_manager.proxies)}")
        
        # Мониторинг прогресса
        last_report = 0
        while self.stats['success'] < self.target_views and not self.stop_event.is_set():
            time.sleep(8)
            if self.stats['success'] > last_report:
                percent = int(self.stats['success'] / self.target_views * 100)
                try:
                    self.bot.send_message(
                        chat_id=self.chat_id,
                        text=f"📊 Прогресс: {self.stats['success']}/{self.target_views} ({percent}%)\n"
                             f"✅ Успешно: {self.stats['success']}\n"
                             f"❌ Ошибок: {self.stats['failed']}\n"
                             f"🔧 Прокси в пуле: {len(self.proxy_manager.proxies)}"
                    )
                except:
                    pass
                last_report = self.stats['success']
        
        # Останавливаем потоки
        self.stop_event.set()
        for t in threads:
            t.join(timeout=2)
        
        result = f"✅ Накрутка завершена!\n\n📌 Пост: {self.post_url}\n🎯 Цель: {self.target_views}\n✅ Успешно: {self.stats['success']}\n❌ Ошибок: {self.stats['failed']}"
        
        if self.stats['success'] == 0:
            result += "\n\n⚠️ Не удалось накрутить ни одного просмотра. Попробуйте позже."
        
        return result

# ========== КОМАНДЫ БОТА ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Проверка админа
    if ADMIN_IDS and update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ У вас нет доступа к этому боту")
        return ConversationHandler.END
    
    await update.message.reply_text(
        "🤖 *Бот для накрутки просмотров Telegram*\n\n"
        "📌 *Как использовать:*\n"
        "1. Отправьте мне ссылку на пост\n"
        "2. Введите количество просмотров (до 5000 за раз)\n"
        "3. Бот запустит накрутку и будет сообщать о прогрессе\n\n"
        "📝 *Пример ссылки:*\n"
        "`https://t.me/durov/123`\n\n"
        "🚀 Отправьте ссылку, чтобы начать!",
        parse_mode='Markdown'
    )
    return WAITING_FOR_LINK

async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    link = update.message.text.strip()
    
    # Проверка ссылки
    if not link.startswith('https://t.me/'):
        await update.message.reply_text("❌ Неверная ссылка. Пример: `https://t.me/durov/123`", parse_mode='Markdown')
        return WAITING_FOR_LINK
    
    parts = link.split('/')
    if len(parts) < 5:
        await update.message.reply_text("❌ Неверный формат. Пример: `https://t.me/durov/123`", parse_mode='Markdown')
        return WAITING_FOR_LINK
    
    context.user_data['post_url'] = link
    await update.message.reply_text(
        f"📌 Пост принят:\n`{link}`\n\n"
        f"📊 Введите количество просмотров (до 5000):",
        parse_mode='Markdown'
    )
    return WAITING_FOR_COUNT

async def handle_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        count = int(update.message.text.strip())
        if count <= 0 or count > 5000:
            await update.message.reply_text("❌ Введите число от 1 до 5000")
            return WAITING_FOR_COUNT
    except:
        await update.message.reply_text("❌ Введите число (например: 500)")
        return WAITING_FOR_COUNT
    
    post_url = context.user_data.get('post_url')
    if not post_url:
        await update.message.reply_text("❌ Ошибка: ссылка не найдена. Начните заново командой /start")
        return ConversationHandler.END
    
    # Отправляем подтверждение
    await update.message.reply_text(
        f"✅ Задача принята!\n\n"
        f"📌 Пост: {post_url}\n"
        f"🎯 Цель: {count} просмотров\n\n"
        f"⏳ Идет подготовка (сбор прокси)..."
    )
    
    # Запускаем накрутку в отдельном потоке
    booster = ViewBooster(post_url, count, update.effective_chat.id, context.bot)
    
    def run_and_report():
        result = booster.run()
        try:
            context.bot.send_message(chat_id=update.effective_chat.id, text=result)
        except Exception as e:
            logger.error(f"Ошибка отправки финального отчета: {e}")
    
    thread = threading.Thread(target=run_and_report)
    thread.daemon = True
    thread.start()
    
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Действие отменено. Отправьте /start, чтобы начать заново.")
    return ConversationHandler.END

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 *Команды бота:*\n\n"
        "/start - начать накрутку\n"
        "/help - показать справку\n"
        "/status - статус бота\n\n"
        "*Как работает:*\n"
        "1. Отправьте ссылку на пост (например, https://t.me/durov/123)\n"
        "2. Укажите количество просмотров (до 5000)\n"
        "3. Бот соберет бесплатные прокси и запустит накрутку\n\n"
        "*Важно:*\n"
        "• Бесплатные прокси работают нестабильно\n"
        "• Если прокси не найдены, повторите через 10-20 минут\n"
        "• 5 просмотров с одного IP, затем смена",
        parse_mode='Markdown'
    )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "✅ Бот работает и готов к выполнению задач!\n\n"
        "📌 Отправьте /start, чтобы начать накрутку.\n\n"
        "⚠️ Бесплатные прокси обновляются каждые 10 минут.\n"
        "Если нет рабочих прокси, повторите попытку позже."
    )

# ========== ЗАПУСК ==========
def main():
    app = Application.builder().token(BOT_TOKEN).build()
    
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            WAITING_FOR_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link)],
            WAITING_FOR_COUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_count)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )
    
    app.add_handler(conv_handler)
    app.add_handler(CommandHandler('help', help_command))
    app.add_handler(CommandHandler('status', status))
    
    logger.info("Бот запущен! Готов к работе.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
