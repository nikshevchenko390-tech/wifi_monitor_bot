#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Чат-бот для мониторинга состояния локальной беспроводной сети
Поддерживает Windows и Linux
Версия 4.0 - Дипломная работа
"""

import os
import re
import subprocess
import logging
import platform
import sqlite3
import asyncio
from concurrent.futures import ThreadPoolExecutor
from dotenv import load_dotenv
from vkbottle import Bot
from vkbottle.bot import Message
from vkbottle import Keyboard, KeyboardButtonColor, Text

# ==================== ЗАГРУЗКА ТОКЕНА ====================
load_dotenv()
TOKEN = os.getenv("VK_TOKEN")

if not TOKEN:
    raise ValueError("Токен не найден. Создайте файл .env с переменной VK_TOKEN")

# ==================== НАСТРОЙКА ЛОГИРОВАНИЯ ====================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('wifi_bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ==================== ИНИЦИАЛИЗАЦИЯ БОТА ====================

bot = Bot(token=TOKEN)
executor = ThreadPoolExecutor(max_workers=4)

# ==================== РУССКАЯ КЛАВИАТУРА ====================

def get_main_keyboard():
    """Создаёт клавиатуру с русскими кнопками"""
    keyboard = Keyboard()
    
    # Первая строка
    keyboard.add(Text("📶 Статус Wi-Fi"), color=KeyboardButtonColor.PRIMARY)
    keyboard.add(Text("📊 Уровень сигнала"), color=KeyboardButtonColor.PRIMARY)
    keyboard.add(Text("👥 Устройства в сети"), color=KeyboardButtonColor.PRIMARY)
    keyboard.row()
    
    # Вторая строка
    keyboard.add(Text("📉 Статистика"), color=KeyboardButtonColor.PRIMARY)
    keyboard.add(Text("🌐 Проверить пинг"), color=KeyboardButtonColor.PRIMARY)
    keyboard.row()
    
    # Третья строка
    keyboard.add(Text("Помощь"), color=KeyboardButtonColor.PRIMARY)
    
    return keyboard

# ==================== БАЗА ДАННЫХ ====================

class Database:
    def __init__(self, db_path='wifi_monitor.db'):
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.create_tables()
        logger.info("База данных инициализирована")
    
    def create_tables(self):
        # Таблица для хранения истории измерений Wi-Fi
        self.conn.execute('''
            CREATE TABLE IF NOT EXISTS wifi_status (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                ssid TEXT,
                signal_strength INTEGER,
                channel INTEGER,
                frequency TEXT
            )
        ''')
        
        # Таблица для хранения истории обнаруженных устройств
        self.conn.execute('''
            CREATE TABLE IF NOT EXISTS connected_devices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                ip TEXT,
                mac TEXT
            )
        ''')
        
        self.conn.commit()
    
    def save_wifi_status(self, ssid, signal, channel, frequency):
        self.conn.execute(
            "INSERT INTO wifi_status (ssid, signal_strength, channel, frequency) VALUES (?, ?, ?, ?)",
            (ssid, signal, channel, frequency)
        )
        self.conn.commit()
    
    def get_wifi_history(self, hours=24):
        cursor = self.conn.execute(
            "SELECT timestamp, signal_strength, ssid FROM wifi_status WHERE timestamp > datetime('now', ?) ORDER BY timestamp",
            (f'-{hours} hours',)
        )
        return cursor.fetchall()
    
    def get_wifi_stats(self, hours=168):
        cursor = self.conn.execute(
            "SELECT signal_strength FROM wifi_status WHERE timestamp > datetime('now', ?)",
            (f'-{hours} hours',)
        )
        return [row[0] for row in cursor.fetchall()]
    
    def save_device(self, ip, mac):
        self.conn.execute(
            "INSERT INTO connected_devices (ip, mac) VALUES (?, ?)",
            (ip, mac)
        )
        self.conn.commit()

db = Database()

# ==================== ОПРЕДЕЛЕНИЕ ОС ====================

def get_os_type():
    system = platform.system()
    if system == "Windows":
        return "windows"
    elif system == "Linux":
        return "linux"
    return "unknown"

def find_wireless_interface():
    iw_result = subprocess.run(
        ["iwconfig"], capture_output=True, text=True, timeout=5
    )
    for line in iw_result.stdout.split('\n'):
        if 'IEEE 802.11' in line and 'ESSID:' in line and 'off/any' not in line:
            return line.split()[0]
    return None

# ==================== ФУНКЦИЯ PING ====================

def ping_host(host="8.8.8.8", count=4):
    os_type = get_os_type()
    
    try:
        if os_type == "windows":
            result = subprocess.run(
                ["ping", "-n", str(count), host],
                capture_output=True,
                text=True,
                encoding="cp866",
                timeout=10
            )
        else:
            result = subprocess.run(
                ["ping", "-c", str(count), host],
                capture_output=True,
                text=True,
                timeout=10
            )
        
        output = result.stdout
        times = re.findall(r'время[=<](\d+)[мс]', output)
        if not times:
            times = re.findall(r'time[=]<(\d+)', output)
        if not times:
            times = re.findall(r'time[=](\d+)', output)
        
        if times:
            avg_time = sum(int(t) for t in times) // len(times)
            return f"✅ {avg_time} мс (успешно: {len(times)}/{count})"
        else:
            return f"❌ Не удалось пропинговать {host}"
            
    except Exception as e:
        return f"⚠️ Ошибка: {e}"

# ==================== ПОЛУЧЕНИЕ СТАТУСА Wi-Fi ====================

def get_wifi_status():
    os_type = get_os_type()
    
    try:
        if os_type == "windows":
            result = subprocess.run(
                ["netsh", "wlan", "show", "interfaces"],
                capture_output=True,
                text=True,
                encoding="cp866",
                timeout=10
            )
            output = result.stdout
            
            if "нет беспроводного интерфейса" in output.lower() or "не найден" in output.lower():
                return "❌ Wi-Fi адаптер не найден или отключён."
            
            ssid = re.search(r"SSID\s*:\s*(.+)", output)
            signal = re.search(r"Сигнал\s*:\s*(\d+)%", output)
            channel = re.search(r"Канал\s*:\s*(\d+)", output)
            speed_rx = re.search(r"Скорость приема \(Мбит/с\)\s*:\s*(\d+)", output)
            speed_tx = re.search(r"Скорость передачи \(Мбит/с\)\s*:\s*(\d+)", output)
            radio_type = re.search(r"Тип радио\s*:\s*(.+)", output)
            
            if not ssid:
                return "❌ Не удалось определить SSID. Возможно, Wi-Fi отключён."
            
            ssid_value = ssid.group(1).strip()
            signal_value = int(signal.group(1)) if signal else 0
            channel_value = int(channel.group(1)) if channel else 0
            radio = radio_type.group(1).strip() if radio_type else ""
            frequency = "5 ГГц" if "802.11ac" in radio or "802.11a" in radio else "2.4 ГГц"
            
            db.save_wifi_status(ssid_value, signal_value, channel_value, frequency)
            
            return f"""📶 Состояние Wi-Fi сети

📡 Имя сети (SSID): {ssid_value}
📊 Уровень сигнала: {signal_value}%
📻 Канал: {channel_value}
📶 Тип сети: {radio if radio else 'Н/Д'}
📥 Скорость приёма: {speed_rx.group(1) if speed_rx else 'Н/Д'} Мбит/с
📤 Скорость передачи: {speed_tx.group(1) if speed_tx else 'Н/Д'} Мбит/с
🌐 Диапазон: {frequency}"""
            
        elif os_type == "linux":
            interface = find_wireless_interface()
            if not interface:
                return "❌ Беспроводной интерфейс не найден или Wi-Fi отключён."
            
            result = subprocess.run(["iwconfig", interface], capture_output=True, text=True, timeout=5)
            output = result.stdout
            
            ssid_match = re.search(r'ESSID:"(.+)"', output)
            signal_match = re.search(r'Signal level=(-\d+) dBm', output)
            freq_match = re.search(r'Frequency:([\d.]+) GHz', output)
            bitrate_match = re.search(r'Bit Rate=([\d.]+) Mb/s', output)
            
            ssid = ssid_match.group(1) if ssid_match else None
            
            if not ssid:
                return "❌ Не удалось определить SSID. Возможно, Wi-Fi отключён."
            
            signal_dbm = int(signal_match.group(1)) if signal_match else -100
            signal_percent = max(0, min(100, int((signal_dbm + 90) / 70 * 100)))
            
            freq = float(freq_match.group(1)) if freq_match else 0
            if 2.4 <= freq <= 2.5:
                frequency = "2.4 ГГц"
            elif 5.0 <= freq <= 5.9:
                frequency = "5 ГГц"
            else:
                frequency = f"{freq} ГГц"
            
            db.save_wifi_status(ssid, signal_percent, 0, frequency)
            
            return f"""📶 Состояние Wi-Fi сети

📡 Имя сети (SSID): {ssid}
📊 Уровень сигнала: {signal_percent}% ({signal_dbm} dBm)
📻 Частота: {frequency}
📥📤 Скорость: {bitrate_match.group(1) if bitrate_match else 'Н/Д'} Мбит/с"""
        
        else:
            return "❌ Операционная система не поддерживается."
            
    except Exception as e:
        logger.error(f"Ошибка в get_wifi_status: {e}")
        return f"⚠️ Ошибка при получении статуса: {e}"

# ==================== ПОЛУЧЕНИЕ УРОВНЯ СИГНАЛА ====================

def get_signal_strength():
    os_type = get_os_type()
    
    try:
        if os_type == "windows":
            result = subprocess.run(["netsh", "wlan", "show", "interfaces"], capture_output=True, text=True, encoding="cp866", timeout=10)
            output = result.stdout
            
            ssid = re.search(r"SSID\s*:\s*(.+)", output)
            signal = re.search(r"Сигнал\s*:\s*(\d+)%", output)
            
            if not ssid:
                return 0, "❌ Wi-Fi не подключён."
                
            signal_pct = int(signal.group(1)) if signal else 0
            ssid_value = ssid.group(1).strip()
            
        elif os_type == "linux":
            interface = find_wireless_interface()
            if not interface:
                return 0, "❌ Wi-Fi не подключён."
                
            result = subprocess.run(["iwconfig", interface], capture_output=True, text=True, timeout=5)
            output = result.stdout
            
            ssid_match = re.search(r'ESSID:"(.+)"', output)
            signal_match = re.search(r'Signal level=(-\d+) dBm', output)
            
            ssid_value = ssid_match.group(1) if ssid_match else None
            signal_dbm = int(signal_match.group(1)) if signal_match else -100
            signal_pct = max(0, min(100, int((signal_dbm + 90) / 70 * 100)))
            
            if not ssid_value:
                return 0, "❌ Wi-Fi не подключён."
                
        else:
            return 0, "❌ Операционная система не поддерживается."
        
        if signal_pct >= 80:
            quality_text = "отличный сигнал"
        elif signal_pct >= 60:
            quality_text = "хороший сигнал"
        elif signal_pct >= 40:
            quality_text = "средний сигнал"
        elif signal_pct >= 20:
            quality_text = "слабый сигнал"
        else:
            quality_text = "очень слабый сигнал"
        
        return signal_pct, f"📶 Уровень вашего сигнала: {signal_pct}% — это {quality_text}"
        
    except Exception as e:
        logger.error(f"Ошибка в get_signal_strength: {e}")
        return 0, f"⚠️ Ошибка при получении уровня сигнала: {e}"

# ==================== ПОЛУЧЕНИЕ СПИСКА УСТРОЙСТВ ====================

def get_arp_table():
    os_type = get_os_type()
    
    try:
        devices = []
        
        if os_type == "windows":
            result = subprocess.run(["arp", "-a"], capture_output=True, text=True, encoding="cp866", timeout=10)
            output = result.stdout
            lines = output.strip().split("\n")
            
            for line in lines:
                match = re.match(r"\s*(\d+\.\d+\.\d+\.\d+)\s+([0-9a-fA-F\-]{17})\s+(\S+)", line)
                if match:
                    ip = match.group(1)
                    mac = match.group(2).replace("-", ":").upper()
                    devices.append((ip, mac))
                    db.save_device(ip, mac)
                    
        elif os_type == "linux":
            result = subprocess.run(["ip", "neigh"], capture_output=True, text=True, timeout=10)
            output = result.stdout
            lines = output.strip().split("\n")
            
            for line in lines:
                match = re.search(r"(\d+\.\d+\.\d+\.\d+).*lladdr\s+([0-9a-fA-F:]{17}).*(REACHABLE|STALE)", line)
                if match:
                    ip = match.group(1)
                    mac = match.group(2).upper()
                    devices.append((ip, mac))
                    db.save_device(ip, mac)
        
        return devices
        
    except Exception as e:
        logger.error(f"Ошибка в get_arp_table: {e}")
        return []

def get_arp_table_formatted():
    devices = get_arp_table()
    if not devices:
        return "👥 Устройства не обнаружены или ARP-таблица пуста."
    
    result = "👥 Подключённые устройства:\n\n"
    for ip, mac in devices:
        result += f"• `{ip}` — {mac}\n"
    return result

# ==================== ОБРАБОТЧИКИ КОМАНД ====================

@bot.on.message(text=["/start", "🚀 Старт", "Начать"])
async def start_handler(message: Message):
    os_type = get_os_type()
    history_count = len(db.get_wifi_history(24))
    keyboard = get_main_keyboard()
    await message.answer(
        f"👋 Привет! Я бот для проверки Wi-Fi сети.\n\n"
        f"🖥️ Ваша операционная система: {os_type}\n"
        f"📊 Сохранено записей: {history_count}\n\n"
        f"Нажимайте на кнопки ниже — и я покажу состояние сети.",
        keyboard=keyboard
    )

@bot.on.message(text=["Помощь", "/help"])
async def help_handler(message: Message):
    help_text = """
1) Статус Wi-Fi — показывает имя сети, уровень сигнала, канал и скорость
2) Уровень сигнала — показывает качество связи
3) Устройства в сети — список всех устройств в вашем Wi-Fi
4) Статистика — средний сигнал за неделю и рекомендации
5) Пинг — проверка задержки до интернета и роутера
"""
    keyboard = get_main_keyboard()
    await message.answer(help_text, keyboard=keyboard)

@bot.on.message(text="📶 Статус Wi-Fi")
async def status_handler(message: Message):
    await message.answer("🔍 Собираю информацию о сети, подождите...")
    status = await asyncio.get_event_loop().run_in_executor(executor, get_wifi_status)
    keyboard = get_main_keyboard()
    await message.answer(status, keyboard=keyboard)

@bot.on.message(text="👥 Устройства в сети")
async def clients_handler(message: Message):
    await message.answer("🔍 Сканирую подключённые устройства, подождите...")
    clients = await asyncio.get_event_loop().run_in_executor(executor, get_arp_table_formatted)
    keyboard = get_main_keyboard()
    await message.answer(clients, keyboard=keyboard)

@bot.on.message(text="📊 Уровень сигнала")
async def signal_handler(message: Message):
    await message.answer("🔍 Измеряю уровень сигнала, подождите...")
    signal_pct, signal_text = await asyncio.get_event_loop().run_in_executor(executor, get_signal_strength)
    keyboard = get_main_keyboard()
    await message.answer(signal_text, keyboard=keyboard)

@bot.on.message(text="📉 Статистика")
async def stats_handler(message: Message):
    await message.answer("📊 Собираю статистику за 7 дней...")
    
    signals = db.get_wifi_stats(168)
    
    if not signals:
        await message.answer("❌ Недостаточно данных для статистики.")
        return
    
    avg_signal = sum(signals) / len(signals)
    max_signal = max(signals)
    min_signal = min(signals)
    
    stats_text = f"""📊 Статистика сети за 7 дней

📡 Средний сигнал: {avg_signal:.1f}%
📈 Максимальный сигнал: {max_signal}%
📉 Минимальный сигнал: {min_signal}%

Подсказка: сигнал выше 60% — это хорошо. Ниже 40% — возможны проблемы.
"""
    
    keyboard = get_main_keyboard()
    await message.answer(stats_text, keyboard=keyboard)

@bot.on.message(text="🌐 Проверить пинг")
async def ping_handler(message: Message):
    await message.answer("📡 Проверяю соединение, подождите...")
    
    internet_ping = await asyncio.get_event_loop().run_in_executor(executor, ping_host, "8.8.8.8", 4)
    router_ping = await asyncio.get_event_loop().run_in_executor(executor, ping_host, "192.168.1.1", 2)
    
    result = f"🌐 Пинг до интернета (Google):\n{internet_ping}\n\n"
    result += f"🏠 Пинг до роутера:\n{router_ping}\n\n"
    result += "Чем меньше цифра, тем быстрее интернет."
    
    keyboard = get_main_keyboard()
    await message.answer(result, keyboard=keyboard)

@bot.on.message()
async def unknown_handler(message: Message):
    keyboard = get_main_keyboard()
    await message.answer(
        f"Непонятная команда: «{message.text}»\n\n"
        f"Пожалуйста, нажимайте на кнопки внизу.",
        keyboard=keyboard
    )

# ==================== ЗАПУСК ====================

if __name__ == "__main__":
    os_type = get_os_type()
    print("=" * 50)
    print("Бот мониторинга Wi-Fi сети запущен...")
    print(f"Платформа: ВКонтакте (VK)")
    print(f"Операционная система: {os_type}")
    print(f"База данных: wifi_monitor.db")
    print(f"Для остановки нажмите Ctrl+C")
    print("=" * 50)
    logger.info(f"Бот запущен на ОС: {os_type}")
    
    bot.run_forever()
