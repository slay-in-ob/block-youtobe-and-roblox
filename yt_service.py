import win32service
import win32serviceutil
import win32event
import servicemanager
import telebot
from telebot import types
import subprocess
import os
import sys
import threading
import time
import configparser
import sqlite3
import datetime

# ================= НАСТРОЙКИ ФАЙЛОВ =================
config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.ini')
sites_file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'sites.txt')
db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'stats.db')

DEFAULT_DOMAINS = [
    "youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be",
    "music.youtube.com", "gaming.youtube.com",
    "ytimg.com", "i.ytimg.com",
    "googlevideo.com",
    "roblox.com", "www.roblox.com", "api.roblox.com", "clientsettings.roblox.com"
]
# ====================================================

config = configparser.ConfigParser()

# Инициализация дефолтных значений, если секций нет
if not os.path.exists(config_path):
    with open(config_path, 'w', encoding='utf-8') as f:
        f.write("[SETTINGS]\nTOKEN = \nUSER_IDS = \n\n[TIMERS]\nBLOCK_MINS = 30\nALLOW_MINS = 30\n\n[LIMITS]\nDAILY_LIMIT_MINS = 0")

try:
    config.read(config_path)
    TOKEN = config.get('SETTINGS', 'TOKEN')
    raw_ids = config.get('SETTINGS', 'USER_IDS')
    ALLOWED_IDS = [int(uid.strip()) for uid in raw_ids.split(',') if uid.strip().isdigit()]
    HOSTS_PATH = r"C:\Windows\System32\drivers\etc\hosts"
    SERVICE_NAME = "YouTubeBlockerBot"
    
    # Читаем таймеры, если нет - ставим дефолт
    try:
        TIMER_BLOCK_MINS = int(config.get('TIMERS', 'BLOCK_MINS'))
        TIMER_ALLOW_MINS = int(config.get('TIMERS', 'ALLOW_MINS'))
    except:
        TIMER_BLOCK_MINS = 30
        TIMER_ALLOW_MINS = 30
        config['TIMERS'] = {'BLOCK_MINS': '30', 'ALLOW_MINS': '30'}
        with open(config_path, 'w') as configfile: config.write(configfile)

    # Читаем лимит, если нет - 0 (без лимита)
    try:
        DAILY_LIMIT_MINS = int(config.get('LIMITS', 'DAILY_LIMIT_MINS'))
    except:
        DAILY_LIMIT_MINS = 0
        config['LIMITS'] = {'DAILY_LIMIT_MINS': '0'}
        with open(config_path, 'w') as configfile: config.write(configfile)

except Exception as e:
    print(f"КРИТИЧЕСКАЯ ОШИБКА: Не удалось прочитать config.ini! {e}")
    sys.exit(1)

# ================= БАЗА ДАННЫХ СТАТИСТИКИ =================
def init_db():
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS logs (timestamp REAL, action TEXT, user_id INTEGER)''')
    conn.commit()
    conn.close()

def log_action(action, user_id):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("INSERT INTO logs VALUES (?, ?, ?)", (time.time(), action, user_id))
    conn.commit()
    conn.close()

def get_usage_seconds_today():
    """Возвращает потраченные секунды за сегодня"""
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    today_start = datetime.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    c.execute("SELECT * FROM logs WHERE timestamp >= ? ORDER BY timestamp", (today_start,))
    rows = c.fetchall()
    conn.close()
    
    total_seconds = 0
    last_unblock_time = None
    for row in rows:
        ts, action, uid = row
        if action == 'UNBLOCK':
            last_unblock_time = ts
        elif action == 'BLOCK' and last_unblock_time is not None:
            total_seconds += (ts - last_unblock_time)
            last_unblock_time = None
            
    if rows and rows[-1][1] == 'UNBLOCK':
        total_seconds += (time.time() - rows[-1][0])
        
    return total_seconds

def check_limit():
    """Проверяет, не превышен ли лимит"""
    if DAILY_LIMIT_MINS == 0: return False # Лимит отключен
    used_sec = get_usage_seconds_today()
    limit_sec = DAILY_LIMIT_MINS * 60
    return used_sec >= limit_sec

def get_stats_text():
    used_sec = get_usage_seconds_today()
    hours = int(used_sec // 3600)
    minutes = int((used_sec % 3600) // 60)
    seconds = int(used_sec % 60)
    
    txt = f"⏱ Использовано сегодня: {hours}ч {minutes}м {seconds}с"
    if DAILY_LIMIT_MINS > 0:
        left = (DAILY_LIMIT_MINS * 60) - used_sec
        if left > 0:
            lh = int(left // 3600)
            lm = int((left % 3600) // 60)
            txt += f"\n⏳ Лимит: осталось {lh}ч {lm}м"
        else:
            txt += "\n🚫 Лимит ИСЧЕРПАН!"
    return txt
# ====================================================

# --- РАБОТА С ФАЙЛОМ СПИСКА САЙТОВ ---
def get_sites():
    if not os.path.exists(sites_file_path):
        with open(sites_file_path, 'w', encoding='utf-8') as f:
            for domain in DEFAULT_DOMAINS:
                f.write(domain + "\n")
    
    with open(sites_file_path, 'r', encoding='utf-8') as f:
        return [line.strip() for line in f if line.strip()]

def add_site_to_file(domain):
    sites = get_sites()
    domain = domain.replace("http://", "").replace("https://", "").replace("www.", "").split('/')[0]
    if domain not in sites:
        with open(sites_file_path, 'a', encoding='utf-8') as f:
            f.write(domain + "\n")
        return True
    return False

def remove_site_from_file(domain):
    sites = get_sites()
    if domain in sites:
        sites.remove(domain)
        with open(sites_file_path, 'w', encoding='utf-8') as f:
            for s in sites:
                f.write(s + "\n")
        return True
    return False

# ================================================

stop_event = threading.Event()
auto_stop_event = threading.Event() 

def kill_roblox():
    try:
        processes = ["RobloxPlayerLauncher.exe", "RobloxPlayerBeta.exe"]
        for proc in processes:
            subprocess.run(['taskkill', '/F', '/IM', proc], 
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        return True
    except:
        return False

def flush_dns_invisible():
    try:
        subprocess.run(['net', 'stop', 'dnscache'], shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(['net', 'start', 'dnscache'], shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except:
        subprocess.run(['ipconfig', '/flushdns'], shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True

# --- ЛОГИКА БЛОКИРОВКИ С ЛОГАМИ ---
def block_all_sites(triggered_by=0):
    try:
        domains = get_sites()
        with open(HOSTS_PATH, 'r') as file: content = file.read()
        changes = False
        lines_to_add = []
        for domain in domains:
            if domain not in content:
                lines_to_add.append(f"127.0.0.1 {domain}")
                lines_to_add.append(f"::1 {domain}")
                changes = True
        if changes:
            with open(HOSTS_PATH, 'a') as file:
                for line in lines_to_add: file.write(line + "\n")
            flush_dns_invisible()
            kill_roblox()
            log_action('BLOCK', triggered_by)
        return changes
    except Exception as e:
        raise Exception(f"Ошибка записи в hosts: {e}")

def unblock_all_sites(triggered_by=0):
    try:
        domains = get_sites()
        with open(HOSTS_PATH, 'r') as file: lines = file.readlines()
        with open(HOSTS_PATH, 'w') as file:
            changes = False
            for line in lines:
                if not any(domain in line for domain in domains): file.write(line)
                else: changes = True
        if changes:
            flush_dns_invisible()
            log_action('UNBLOCK', triggered_by)
        return changes
    except Exception as e:
        raise Exception(f"Ошибка записи в hosts: {e}")

def unblock_single_site(domain):
    try:
        with open(HOSTS_PATH, 'r') as file: lines = file.readlines()
        with open(HOSTS_PATH, 'w') as file:
            changes = False
            for line in lines:
                if domain not in line: file.write(line)
                else: changes = True
        if changes:
            flush_dns_invisible()
        return changes
    except Exception as e:
        raise Exception(f"Ошибка удаления {domain}: {e}")

# --- НИТЬ АВТО-ТАЙМЕРА (С ЛИМИТАМИ) ---
def auto_timer_thread(bot_instance):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(types.KeyboardButton("⏹️ Остановить таймер"), types.KeyboardButton("📊 Статус"))
    
    try:
        while not auto_stop_event.is_set() and not stop_event.is_set():
            # Проверка лимита перед открытием доступа
            limit_reached = check_limit()
            
            if limit_reached:
                block_all_sites(triggered_by=0)
                for uid in ALLOWED_IDS:
                    try:
                        bot_instance.send_message(uid, "🚫 <b>ЛИМИТ ИСЧЕРПАН!</b>\nДоступ невозможен до завтра.", parse_mode='HTML', reply_markup=markup)
                    except: pass
                
                # Если лимит есть, ждем 60 секунд и проверяем снова (или можно ждать до 00:00)
                if auto_stop_event.wait(60): break
                if stop_event.is_set(): break
                continue # Переход к следующей итерации (повторная проверка)
            
            # Если лимита нет или он не достигнут - открываем доступ
            unblock_all_sites(triggered_by=0)
            for uid in ALLOWED_IDS:
                try:
                    bot_instance.send_message(uid, f"⏳ Сайты <b>ДОСТУПНЫ</b> ({TIMER_ALLOW_MINS} мин).\n{get_stats_text()}", parse_mode='HTML', reply_markup=markup)
                except: pass
            
            if auto_stop_event.wait(TIMER_ALLOW_MINS * 60): break
            if stop_event.is_set(): break

            # ФАЗА БЛОКИРОВКИ
            block_all_sites(triggered_by=0)
            for uid in ALLOWED_IDS:
                try:
                    bot_instance.send_message(uid, f"🚫 Сайты <b>ЗАБЛОКИРОВАНЫ</b> ({TIMER_BLOCK_MINS} мин).\n🎮 Roblox закрыт.", parse_mode='HTML', reply_markup=markup)
                except: pass

            if auto_stop_event.wait(TIMER_BLOCK_MINS * 60): break
            if stop_event.is_set(): break

    except Exception as e:
        servicemanager.LogErrorMsg(f"Ошибка таймера: {e}")
    finally:
        main_markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
        main_markup.add(types.KeyboardButton("🚫 Блокировать"), types.KeyboardButton("🔓 Разблокировать"))
        main_markup.add(types.KeyboardButton("🔄 Таймер"), types.KeyboardButton("📊 Статус"))
        
        for uid in ALLOWED_IDS:
            try:
                bot_instance.send_message(uid, "⏹️ Авто-таймер остановлен.", reply_markup=main_markup)
            except: pass

# --- ЗАПУСК БОТА ---
def run_bot_thread():
    bot = telebot.TeleBot(TOKEN)
    globals()['bot_ref'] = bot

    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(types.KeyboardButton("🚫 Блокировать"), types.KeyboardButton("🔓 Разблокировать"))
    markup.add(types.KeyboardButton("🔄 Таймер"), types.KeyboardButton("📊 Статус"))

    @bot.message_handler(commands=['start', 'help'])
    def send_welcome(message):
        if message.chat.id not in ALLOWED_IDS: return
        help_text = (
            f"Бот готов.\n\n"
            f"⏰ Таймер: {TIMER_BLOCK_MINS}мин / {TIMER_ALLOW_MINS}мин\n"
            f"⏱ Лимит сегодня: {DAILY_LIMIT_MINS} мин (0 = нет)\n\n"
            "Команды:\n"
            "/timer [блок] [доступ] - изменить интервалы\n"
            "/limit [минуты] - лимит времени\n"
            "/add /remove /list - сайты"
        )
        bot.reply_to(message, help_text, reply_markup=markup)

    @bot.message_handler(commands=['timer'])
    def set_timer(message):
        if message.chat.id not in ALLOWED_IDS: return
        try:
            args = message.text.split()
            if len(args) < 3:
                bot.reply_to(message, f"⚠️ Пример: /timer 50 10 (50мин блок, 10мин доступ)", reply_markup=markup)
                return
            
            b_mins = int(args[1])
            a_mins = int(args[2])
            
            # Обновляем в памяти и файле
            global TIMER_BLOCK_MINS, TIMER_ALLOW_MINS
            TIMER_BLOCK_MINS = b_mins
            TIMER_ALLOW_MINS = a_mins
            config['TIMERS']['BLOCK_MINS'] = str(b_mins)
            config['TIMERS']['ALLOW_MINS'] = str(a_mins)
            with open(config_path, 'w') as configfile: config.write(configfile)
            
            bot.reply_to(message, f"✅ Таймер обновлен: {b_mins} мин работы / {a_mins} мин отдыха.\nНажмите 'Таймер' для перезапуска.", reply_markup=markup)
        except:
            bot.reply_to(message, "❌ Неверный формат. Используйте числа.", reply_markup=markup)

    @bot.message_handler(commands=['limit'])
    def set_limit(message):
        if message.chat.id not in ALLOWED_IDS: return
        try:
            args = message.text.split()
            if len(args) < 2:
                # Если нет аргументов, показываем текущий
                bot.reply_to(message, f"Текущий лимит: {DAILY_LIMIT_MINS} мин.\nИспользуйте /limit 60 для изменения.", reply_markup=markup)
                return

            mins = int(args[1])
            global DAILY_LIMIT_MINS
            DAILY_LIMIT_MINS = mins
            
            config['LIMITS']['DAILY_LIMIT_MINS'] = str(mins)
            with open(config_path, 'w') as configfile: config.write(configfile)
            
            if mins == 0:
                txt = "✅ Лимит отключен."
            else:
                txt = f"✅ Лимит установлен: {mins} мин в день."
                # Если лимит уже превышен, сразу блокируем
                if check_limit():
                    block_all_sites(triggered_by=message.chat.id)
                    txt += "\n🚫 Лимит уже превышен! Доступ закрыт."
            
            bot.reply_to(message, txt, reply_markup=markup)
        except:
            bot.reply_to(message, "❌ Ошибка формата.", reply_markup=markup)

    @bot.message_handler(commands=['list'])
    def send_list(message):
        if message.chat.id not in ALLOWED_IDS: return
        sites = get_sites()
        if not sites:
            bot.reply_to(message, "Список пуст.", reply_markup=markup)
        else:
            text = "📋 Заблокированные сайты:\n\n" + "\n".join([f"• {s}" for s in sites])
            bot.reply_to(message, text, reply_markup=markup)

    @bot.message_handler(commands=['add'])
    def add_site(message):
        if message.chat.id not in ALLOWED_IDS: return
        try:
            args = message.text.split()
            if len(args) < 2:
                bot.reply_to(message, "⚠️ Пример: /add tiktok.com", reply_markup=markup)
                return
            
            domain = args[1].strip().lower()
            if '.' not in domain:
                bot.reply_to(message, "❌ Неверный формат домена.", reply_markup=markup)
                return

            if add_site_to_file(domain):
                block_all_sites(triggered_by=message.chat.id)
                bot.reply_to(message, f"✅ Добавлен: {domain} (Заблокировано)", reply_markup=markup)
            else:
                bot.reply_to(message, f"ℹ️ {domain} уже есть в списке.", reply_markup=markup)
        except Exception as e:
            bot.reply_to(message, f"Ошибка: {e}", reply_markup=markup)

    @bot.message_handler(commands=['remove'])
    def remove_site(message):
        if message.chat.id not in ALLOWED_IDS: return
        try:
            args = message.text.split()
            if len(args) < 2:
                bot.reply_to(message, "⚠️ Пример: /remove tiktok.com", reply_markup=markup)
                return
            
            domain = args[1].strip().lower()
            
            if remove_site_from_file(domain):
                unblock_single_site(domain)
                bot.reply_to(message, f"🗑 Удален: {domain} (Доступ открыт)", reply_markup=markup)
            else:
                bot.reply_to(message, f"ℹ️ {domain} не найден в списке.", reply_markup=markup)
        except Exception as e:
            bot.reply_to(message, f"Ошибка: {e}", reply_markup=markup)

    @bot.message_handler(func=lambda message: message.text == "🔄 Таймер")
    def handle_start_timer(message):
        if message.chat.id not in ALLOWED_IDS: return
        # Проверка лимита перед запуском
        if check_limit():
             bot.reply_to(message, "🚫 Невозможно запустить таймер: Лимит на сегодня исчерпан!", reply_markup=markup)
             return
             
        auto_stop_event.clear()
        bot.reply_to(message, f"🔄 Таймер запущен ({TIMER_BLOCK_MINS}/{TIMER_ALLOW_MINS}).")
        t = threading.Thread(target=auto_timer_thread, args=(bot,), daemon=True)
        t.start()

    @bot.message_handler(func=lambda message: message.text == "⏹️ Остановить таймер")
    def handle_stop_timer(message):
        if message.chat.id not in ALLOWED_IDS: return
        auto_stop_event.set()

    @bot.message_handler(func=lambda message: message.text == "🚫 Блокировать")
    def handle_block(message):
        if message.chat.id not in ALLOWED_IDS: return
        
        timer_was_running = not auto_stop_event.is_set()
        if timer_was_running: 
            auto_stop_event.set()
            time.sleep(0.5)

        try:
            block_all_sites(triggered_by=message.chat.id)
        except Exception as e:
            bot.reply_to(message, f"Ошибка: {e}")
            return

        main_markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
        main_markup.add(types.KeyboardButton("🚫 Блокировать"), types.KeyboardButton("🔓 Разблокировать"))
        main_markup.add(types.KeyboardButton("🔄 Таймер"), types.KeyboardButton("📊 Статус"))

        for uid in ALLOWED_IDS:
            try:
                if uid == message.chat.id:
                    text = "⚠️ Таймер остановлен.\n✅ Сайты заблокированы Вами." if timer_was_running else "✅ Сайты заблокированы Вами."
                else:
                    text = "⚠️ Таймер остановлен другим пользователем.\n🚫 Сайты заблокированы." if timer_was_running else "🚫 Сайты заблокированы другим пользователем."
                bot.send_message(uid, text, reply_markup=main_markup)
            except: pass

    @bot.message_handler(func=lambda message: message.text == "🔓 Разблокировать")
    def handle_unblock(message):
        if message.chat.id not in ALLOWED_IDS: return
        
        # ПРОВЕРКА ЛИМИТА
        if check_limit():
            bot.reply_to(message, f"🚫 <b>ЛИМИТ ИСЧЕРПАН!</b>\nНевозможно открыть доступ.\n{get_stats_text()}", parse_mode='HTML', reply_markup=markup)
            return

        timer_was_running = not auto_stop_event.is_set()
        if timer_was_running:
            auto_stop_event.set()
            time.sleep(0.5)

        try:
            unblock_all_sites(triggered_by=message.chat.id)
        except Exception as e:
            bot.reply_to(message, f"Ошибка: {e}")
            return

        main_markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
        main_markup.add(types.KeyboardButton("🚫 Блокировать"), types.KeyboardButton("🔓 Разблокировать"))
        main_markup.add(types.KeyboardButton("🔄 Таймер"), types.KeyboardButton("📊 Статус"))

        for uid in ALLOWED_IDS:
            try:
                if uid == message.chat.id:
                    text = "⚠️ Таймер остановлен.\n🔓 Доступ открыт Вами." if timer_was_running else "🔓 Доступ открыт Вами."
                else:
                    text = "⚠️ Таймер остановлен другим пользователем.\n🔓 Доступ открыт." if timer_was_running else "🔓 Доступ открыт другим пользователем."
                bot.send_message(uid, text, reply_markup=main_markup)
            except: pass

    @bot.message_handler(func=lambda message: message.text == "📊 Статус")
    def handle_status(message):
        if message.chat.id not in ALLOWED_IDS: return
        try:
            sites = get_sites()
            with open(HOSTS_PATH, 'r') as file:
                content = file.read()
                blocked = any(domain in content for domain in sites)
                status = "🚫 ЗАБЛОКИРОВАНО" if blocked else "✅ ДОСТУПНО"
                
                is_timer_running = not auto_stop_event.is_set()
                timer_status = f" (Авто-таймер {TIMER_BLOCK_MINS}/{TIMER_ALLOW_MINS})" if is_timer_running else ""
                
                bot.reply_to(message, f"Статус сайтов: {status}{timer_status}\n{get_stats_text()}", reply_markup=markup)
        except Exception as e:
            bot.reply_to(message, f"Ошибка: {e}", reply_markup=markup)

    while not stop_event.is_set():
        try:
            bot.polling(none_stop=True, timeout=10)
        except Exception as e:
            if "Network is unreachable" not in str(e) and "Connection reset" not in str(e):
                servicemanager.LogErrorMsg(f"Ошибка polling: {e}")
            time.sleep(2)

# --- КЛАСС СЛУЖБЫ ---
class YouTubeBlockerService(win32serviceutil.ServiceFramework):
    _svc_name_ = SERVICE_NAME
    _svc_display_name_ = "YouTube Blocker Service"
    _svc_description_ = "Блокировка со статистикой, гибкими таймерами и лимитами."

    def __init__(self, args):
        win32serviceutil.ServiceFramework.__init__(self, args)
        self.hWaitStop = win32event.CreateEvent(None, 0, 0, None)
        self.is_alive = True

    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        win32event.SetEvent(self.hWaitStop)
        self.is_alive = False
        stop_event.set()
        auto_stop_event.set() 
        servicemanager.LogMsg(servicemanager.EVENTLOG_INFORMATION_TYPE,
                              servicemanager.PYS_SERVICE_STOPPED,
                              (self._svc_name_, ''))

    def SvcDoRun(self):
        servicemanager.LogMsg(servicemanager.EVENTLOG_INFORMATION_TYPE,
                              servicemanager.PYS_SERVICE_STARTED,
                              (self._svc_name_, ''))
        init_db()
        bot_thread = threading.Thread(target=run_bot_thread)
        bot_thread.daemon = True
        bot_thread.start()
        win32event.WaitForSingleObject(self.hWaitStop, win32event.INFINITE)

if __name__ == '__main__':
    if len(sys.argv) == 1:
        servicemanager.Initialize()
        servicemanager.PrepareToHostSingle(YouTubeBlockerService)
        servicemanager.StartServiceCtrlDispatcher()
    else:
        win32serviceutil.HandleCommandLine(YouTubeBlockerService)
