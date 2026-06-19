import logging
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from enum import Enum
import random
import json
import pytz
import asyncio
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from telegram import (
    Update, 
    InlineKeyboardButton, 
    InlineKeyboardMarkup, 
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    ChatPermissions
)
from telegram.ext import (
    Application, 
    CommandHandler, 
    ContextTypes, 
    ConversationHandler, 
    CallbackQueryHandler,
    MessageHandler,
    filters
)

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Состояния для ConversationHandler
class States(Enum):
    ADMIN_MAIN = 1
    ADMIN_ADD_CHANNEL = 2
    ADMIN_CHANNEL_SETTINGS = 3
    ADMIN_SET_APPROVAL_TIME = 4
    ADMIN_CUSTOM_TIME = 5
    ADMIN_BROADCAST = 6
    ADMIN_EDIT_TEXT = 7
    ADMIN_ADD_MESSAGE = 8
    ADMIN_ADD_ADVERTISER = 9
    USER_CONFIRMATION = 10
    USER_WAITING_APPROVAL = 11
    USER_CONTACT_ADMIN = 12
    ADMIN_RESPOND_TO_USER = 13

# Инициализация базы данных
def init_db():
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    
    # Таблица каналов
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS channels (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        channel_id INTEGER UNIQUE,
        title TEXT,
        username TEXT,
        invite_link TEXT,
        auto_approve_time INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')
    
    # Таблица пользователей
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER UNIQUE,
        username TEXT,
        first_name TEXT,
        last_name TEXT,
        registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        is_confirmed BOOLEAN DEFAULT FALSE,
        confirmed_at TIMESTAMP
    )
    ''')
    
    # Таблица заявок
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS applications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        channel_id INTEGER,
        status TEXT DEFAULT 'pending',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        processed_at TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users (user_id),
        FOREIGN KEY (channel_id) REFERENCES channels (channel_id)
    )
    ''')
    
    # Таблица сообщений бота
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS bot_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE,
        text TEXT
    )
    ''')
    
    # Таблица рекламодателей
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS advertisers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        channel_id INTEGER,
        title TEXT,
        invite_link TEXT,
        priority INTEGER DEFAULT 0,
        FOREIGN KEY (channel_id) REFERENCES channels (channel_id)
    )
    ''')
    
    # Таблица для связи пользователей с админами
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS user_admin_chats (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        message_text TEXT,
        message_type TEXT DEFAULT 'user_to_admin',
        is_read BOOLEAN DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users (user_id)
    )
    ''')
    
    # Вставляем стандартные сообщения
    default_messages = [
        ('welcome', 'Добро пожаловать! Для доступа к каналу подтвердите, что вы не робот.'),
        ('confirmation', 'Спасибо, вы подтвердили, что вы не робот. Ваша заявка на вступление будет одобрена модераторами.'),
        ('ad_small', 'Подпишитесь на наши партнерские каналы:'),
        ('admin_contact', 'Связь с админом')
    ]
    
    cursor.executemany(
        'INSERT OR IGNORE INTO bot_messages (name, text) VALUES (?, ?)',
        default_messages
    )
    
    conn.commit()
    conn.close()

# Функции для работы с базой данных
def get_db_connection():
    return sqlite3.connect('bot_data.db')

def get_channels():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT channel_id, title FROM channels')
    channels = cursor.fetchall()
    conn.close()
    return channels

def add_channel(channel_id, title, username, invite_link):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            'INSERT INTO channels (channel_id, title, username, invite_link) VALUES (?, ?, ?, ?)',
            (channel_id, title, username, invite_link)
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()

def get_channel_settings(channel_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        'SELECT auto_approve_time FROM channels WHERE channel_id = ?',
        (channel_id,)
    )
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else 0

def update_channel_approval_time(channel_id, approval_time):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        'UPDATE channels SET auto_approve_time = ? WHERE channel_id = ?',
        (approval_time, channel_id)
    )
    conn.commit()
    conn.close()

def get_user(user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        'SELECT user_id, username, first_name, last_name, registered_at, is_confirmed FROM users WHERE user_id = ?',
        (user_id,)
    )
    user = cursor.fetchone()
    conn.close()
    return user

def add_user(user_id, username, first_name, last_name):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            'INSERT INTO users (user_id, username, first_name, last_name) VALUES (?, ?, ?, ?)',
            (user_id, username, first_name, last_name)
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()

def confirm_user(user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        'UPDATE users SET is_confirmed = TRUE, confirmed_at = CURRENT_TIMESTAMP WHERE user_id = ?',
        (user_id,)
    )
    conn.commit()
    conn.close()

def add_application(user_id, channel_id):
    """Добавляет заявку пользователя в канал, если её еще нет"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        # Проверяем, есть ли уже заявка от этого пользователя в этом канале
        cursor.execute(
            'SELECT id FROM applications WHERE user_id = ? AND channel_id = ? AND status = "pending"',
            (user_id, channel_id)
        )
        existing_application = cursor.fetchone()
        
        if existing_application:
            # Заявка уже существует
            logger.info(f"Заявка от пользователя {user_id} в канал {channel_id} уже существует")
            return False
        
        # Добавляем новую заявку
        cursor.execute(
            'INSERT INTO applications (user_id, channel_id) VALUES (?, ?)',
            (user_id, channel_id)
        )
        conn.commit()
        logger.info(f"Добавлена новая заявка от пользователя {user_id} в канал {channel_id}")
        return True
        
    except Exception as e:
        logger.error(f"Ошибка при добавлении заявки: {e}")
        return False
    finally:
        conn.close()

def get_message(name):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT text FROM bot_messages WHERE name = ?', (name,))
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else ""

def escape_markdown(text):
    """Экранирует специальные символы для Markdown"""
    if not text:
        return text
    return text.replace('*', '\\*').replace('_', '\\_').replace('[', '\\[').replace(']', '\\]').replace('`', '\\`')

def update_message(name, text):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        'UPDATE bot_messages SET text = ? WHERE name = ?',
        (text, name)
    )
    conn.commit()
    conn.close()

# Функции для системы сообщений пользователь-админ
def add_user_message(user_id, message_text, message_type='user_to_admin'):
    """Добавляет сообщение от пользователя к админу"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO user_admin_chats (user_id, message_text, message_type, created_at)
        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
    ''', (user_id, message_text, message_type))
    conn.commit()
    conn.close()

def get_user_messages(limit=50):
    """Получает последние сообщения от пользователей"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT uac.id, uac.user_id, uac.message_text, uac.message_type, uac.created_at,
               u.username, u.first_name, u.last_name
        FROM user_admin_chats uac
        LEFT JOIN users u ON uac.user_id = u.user_id
        ORDER BY uac.created_at DESC
        LIMIT ?
    ''', (limit,))
    messages = cursor.fetchall()
    conn.close()
    return messages

def get_user_conversation(user_id, limit=20):
    """Получает переписку с конкретным пользователем"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT uac.id, uac.user_id, uac.message_text, uac.message_type, uac.created_at,
               u.username, u.first_name, u.last_name
        FROM user_admin_chats uac
        LEFT JOIN users u ON uac.user_id = u.user_id
        WHERE uac.user_id = ?
        ORDER BY uac.created_at ASC
        LIMIT ?
    ''', (user_id, limit))
    messages = cursor.fetchall()
    conn.close()
    return messages

def get_unread_messages_count():
    """Получает количество непрочитанных сообщений от пользователей"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT COUNT(*) FROM user_admin_chats 
        WHERE message_type = 'user_to_admin' AND is_read = 0
    ''')
    count = cursor.fetchone()[0]
    conn.close()
    return count

def mark_message_as_read(message_id):
    """Отмечает сообщение как прочитанное"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE user_admin_chats SET is_read = 1 WHERE id = ?
    ''', (message_id,))
    conn.commit()
    conn.close()

def clear_user_messages(user_id):
    """Очистить все сообщения пользователя после ответа админа"""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute('DELETE FROM user_admin_chats WHERE user_id = ? AND message_type = "user_to_admin"', (user_id,))
        conn.commit()
        logger.info(f"Очищены сообщения пользователя {user_id}")
    except Exception as e:
        logger.error(f"Ошибка при очистке сообщений пользователя: {e}")
    finally:
        conn.close()

def auto_approve_applications():
    """Автоматически принимает заявки по истечении времени"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        # Получаем все каналы с настроенным временем автопринятия
        cursor.execute('''
            SELECT channel_id, auto_approve_time 
            FROM channels 
            WHERE auto_approve_time > 0
        ''')
        channels_with_auto_approve = cursor.fetchall()
        
        approved_count = 0
        
        for channel_id, auto_approve_time in channels_with_auto_approve:
            # Принимаем заявки старше указанного времени
            cursor.execute('''
                UPDATE applications 
                SET status = "approved", processed_at = CURRENT_TIMESTAMP 
                WHERE channel_id = ? 
                AND status = "pending" 
                AND created_at <= datetime('now', '-' || ? || ' minutes')
            ''', (channel_id, auto_approve_time))
            
            approved_count += cursor.rowcount
        
        conn.commit()
        
        if approved_count > 0:
            logger.info(f"Автоматически принято {approved_count} заявок")
        
        return approved_count
        
    except Exception as e:
        logger.error(f"Ошибка при автоматическом принятии заявок: {e}")
        return 0
    finally:
        conn.close()

# Функции для статистики
def get_general_stats():
    """Получает общую статистику"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        # Общее количество пользователей
        cursor.execute('SELECT COUNT(*) FROM users')
        total_users = cursor.fetchone()[0]
        
        # Подтвержденные пользователи
        cursor.execute('SELECT COUNT(*) FROM users WHERE is_confirmed = TRUE')
        confirmed_users = cursor.fetchone()[0]
        
        # Общее количество каналов
        cursor.execute('SELECT COUNT(*) FROM channels')
        total_channels = cursor.fetchone()[0]
        
        # Общее количество заявок
        cursor.execute('SELECT COUNT(*) FROM applications')
        total_applications = cursor.fetchone()[0]
        
        # Принятые заявки
        cursor.execute('SELECT COUNT(*) FROM applications WHERE status = "approved"')
        approved_applications = cursor.fetchone()[0]
        
        # Ожидающие заявки
        cursor.execute('SELECT COUNT(*) FROM applications WHERE status = "pending"')
        pending_applications = cursor.fetchone()[0]
        
        # Рекламодатели
        cursor.execute('SELECT COUNT(*) FROM advertisers')
        total_advertisers = cursor.fetchone()[0]
        
        return {
            'total_users': total_users,
            'confirmed_users': confirmed_users,
            'total_channels': total_channels,
            'total_applications': total_applications,
            'approved_applications': approved_applications,
            'pending_applications': pending_applications,
            'total_advertisers': total_advertisers
        }
    finally:
        conn.close()

def get_channel_stats(channel_id):
    """Получает статистику для конкретного канала"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        # Информация о канале
        cursor.execute(
            'SELECT title, username, auto_approve_time FROM channels WHERE channel_id = ?',
            (channel_id,)
        )
        channel_info = cursor.fetchone()
        
        if not channel_info:
            return None
        
        title, username, auto_approve_time = channel_info
        
        # Статистика заявок для канала
        cursor.execute(
            'SELECT COUNT(*) FROM applications WHERE channel_id = ?',
            (channel_id,)
        )
        total_applications = cursor.fetchone()[0]
        
        cursor.execute(
            'SELECT COUNT(*) FROM applications WHERE channel_id = ? AND status = "approved"',
            (channel_id,)
        )
        approved_applications = cursor.fetchone()[0]
        
        cursor.execute(
            'SELECT COUNT(*) FROM applications WHERE channel_id = ? AND status = "pending"',
            (channel_id,)
        )
        pending_applications = cursor.fetchone()[0]
        
        # Последние заявки
        cursor.execute(
            '''SELECT u.username, u.first_name, a.created_at, a.status 
               FROM applications a 
               JOIN users u ON a.user_id = u.user_id 
               WHERE a.channel_id = ? 
               ORDER BY a.created_at DESC 
               LIMIT 5''',
            (channel_id,)
        )
        recent_applications = cursor.fetchall()
        
        return {
            'title': title,
            'username': username,
            'auto_approve_time': auto_approve_time,
            'total_applications': total_applications,
            'approved_applications': approved_applications,
            'pending_applications': pending_applications,
            'recent_applications': recent_applications
        }
    finally:
        conn.close()

def get_user_stats():
    """Получает статистику пользователей"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        # Пользователи за последние 7 дней
        cursor.execute(
            '''SELECT COUNT(*) FROM users 
               WHERE registered_at >= datetime('now', '-7 days')'''
        )
        users_last_7_days = cursor.fetchone()[0]
        
        # Пользователи за последние 30 дней
        cursor.execute(
            '''SELECT COUNT(*) FROM users 
               WHERE registered_at >= datetime('now', '-30 days')'''
        )
        users_last_30_days = cursor.fetchone()[0]
        
        # Подтвержденные пользователи за последние 7 дней
        cursor.execute(
            '''SELECT COUNT(*) FROM users 
               WHERE is_confirmed = TRUE AND confirmed_at >= datetime('now', '-7 days')'''
        )
        confirmed_last_7_days = cursor.fetchone()[0]
        
        return {
            'users_last_7_days': users_last_7_days,
            'users_last_30_days': users_last_30_days,
            'confirmed_last_7_days': confirmed_last_7_days
        }
    finally:
        conn.close()

def get_advertisers():
    """Получает список всех рекламодателей"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute('SELECT id, title, invite_link, priority FROM advertisers ORDER BY priority DESC')
        advertisers = cursor.fetchall()
        return advertisers
    finally:
        conn.close()

def delete_advertiser(advertiser_id):
    """Удаляет рекламодателя по ID"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute('DELETE FROM advertisers WHERE id = ?', (advertiser_id,))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()

# Функции для клавиатур
def get_admin_main_keyboard():
    channels = get_channels()
    keyboard = []
    
    if not channels:
        keyboard.append([InlineKeyboardButton("Добавить канал", callback_data="add_channel")])
    else:
        for channel_id, title in channels:
            keyboard.append([InlineKeyboardButton(title, callback_data=f"channel_{channel_id}")])
        keyboard.append([InlineKeyboardButton("Добавить канал", callback_data="add_channel")])
    
    keyboard.append([InlineKeyboardButton("Статистика", callback_data="stats")])
    keyboard.append([InlineKeyboardButton("Рассылка", callback_data="broadcast")])
    keyboard.append([InlineKeyboardButton("Добавить рекламодателя", callback_data="add_advertiser")])
    keyboard.append([InlineKeyboardButton("Просмотр рекламодателей", callback_data="view_advertisers")])
    keyboard.append([InlineKeyboardButton("Изменение текста", callback_data="edit_text")])
    
    # Добавляем кнопку сообщений с счетчиком непрочитанных
    unread_count = get_unread_messages_count()
    messages_text = f"💬 Сообщения ({unread_count})" if unread_count > 0 else "💬 Сообщения"
    keyboard.append([InlineKeyboardButton(messages_text, callback_data="messages")])
    
    return InlineKeyboardMarkup(keyboard)

def get_channel_settings_keyboard(channel_id):
    keyboard = [
        [InlineKeyboardButton("Принятие заявки", callback_data=f"approval_{channel_id}")],
        [InlineKeyboardButton("Статистика", callback_data=f"stats_{channel_id}")],
        [InlineKeyboardButton("« Назад", callback_data="back_to_main")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_approval_settings_keyboard(channel_id):
    keyboard = [
        [InlineKeyboardButton("Моментально", callback_data=f"approve_0_{channel_id}")],
        [InlineKeyboardButton("5 минут", callback_data=f"approve_5_{channel_id}")],
        [InlineKeyboardButton("15 минут", callback_data=f"approve_15_{channel_id}")],
        [InlineKeyboardButton("Указать своё время", callback_data=f"approve_custom_{channel_id}")],
        [InlineKeyboardButton("Не принимать", callback_data=f"approve_none_{channel_id}")],
        [InlineKeyboardButton("Принять всех", callback_data=f"approve_all_{channel_id}")],
        [InlineKeyboardButton("« Назад", callback_data=f"back_to_channel_{channel_id}")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_user_confirmation_keyboard():
    keyboard = [
        [InlineKeyboardButton("Я человек", callback_data="confirm_human")],
        [InlineKeyboardButton("« Назад", callback_data="back_to_main")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_user_main_keyboard():
    keyboard = [
        [InlineKeyboardButton("Связь с админом", callback_data="contact_admin")],
        [InlineKeyboardButton("Испытать удачу", callback_data="try_luck")],
        [InlineKeyboardButton("Профиль", callback_data="profile")],
        [InlineKeyboardButton("« Назад", callback_data="back_to_main")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_back_keyboard():
    keyboard = [[InlineKeyboardButton("« Назад", callback_data="back_to_main")]]
    return InlineKeyboardMarkup(keyboard)

def get_permanent_back_keyboard():
    """Клавиатура с перманентной кнопкой назад"""
    keyboard = [[InlineKeyboardButton("« Назад", callback_data="back_to_main")]]
    return InlineKeyboardMarkup(keyboard)

def get_back_to_edit_keyboard():
    keyboard = [[InlineKeyboardButton("« Назад", callback_data="back_to_edit")]]
    return InlineKeyboardMarkup(keyboard)

def get_back_to_user_keyboard():
    keyboard = [[InlineKeyboardButton("« Назад", callback_data="back_to_user")]]
    return InlineKeyboardMarkup(keyboard)

def get_back_to_approval_keyboard(channel_id):
    keyboard = [[InlineKeyboardButton("« Назад", callback_data=f"back_to_approval_{channel_id}")]]
    return InlineKeyboardMarkup(keyboard)

# Обработчики команд
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    
    # Загружаем конфигурацию
    try:
        with open('config.json', 'r', encoding='utf-8') as f:
            config = json.load(f)
        admin_ids = config.get('admin_ids', [])
    except FileNotFoundError:
        logger.error("Файл config.json не найден")
        admin_ids = []
    except json.JSONDecodeError as e:
        logger.error(f"Ошибка в формате config.json: {e}")
        admin_ids = []
    
    # Проверяем, является ли пользователь администратором
    if user_id in admin_ids:
        await update.message.reply_text(
            "Добро пожаловать в панель администратора!",
            reply_markup=get_admin_main_keyboard()
        )
        return States.ADMIN_MAIN
    else:
        # Добавляем пользователя в базу, если его нет
        if not get_user(user_id):
            add_user(user_id, user.username, user.first_name, user.last_name)
        
        # Отправляем сообщение с подтверждением
        welcome_message = get_message('welcome')
        await update.message.reply_text(
            welcome_message,
            reply_markup=get_user_confirmation_keyboard()
        )
        return States.USER_CONFIRMATION

async def handle_user_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == 'confirm_human':
        user_id = query.from_user.id
        confirm_user(user_id)
        
        # Получаем сообщение подтверждения
        confirmation_message = get_message('confirmation')
        
        # Получаем рекламные каналы
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT title, invite_link FROM advertisers ORDER BY priority DESC')
        advertisers = cursor.fetchall()
        conn.close()
        
        # Формируем сообщение с рекламой
        ad_message = get_message('ad_small')
        if advertisers:
            ad_message += "\n\n"
            for title, invite_link in advertisers:
                # Экранируем специальные символы для Markdown
                safe_title = escape_markdown(title)
                ad_message += f"• [{safe_title}]({invite_link})\n"
        
        # Отправляем сообщение
        await query.edit_message_text(
            f"{confirmation_message}\n\n{ad_message}",
            parse_mode='Markdown',
            reply_markup=get_user_main_keyboard()
        )
        
        # Добавляем заявку во все каналы
        channels = get_channels()
        for channel_id, _ in channels:
            add_application(user_id, channel_id)
        
        return States.USER_WAITING_APPROVAL
    elif query.data == 'back_to_main':
        # Обработка кнопки "Назад" в состоянии подтверждения
        user_id = query.from_user.id
        
        # Загружаем конфигурацию для проверки прав
        try:
            with open('config.json', 'r', encoding='utf-8') as f:
                config = json.load(f)
            admin_ids = config.get('admin_ids', [])
        except:
            admin_ids = []
        
        # Проверяем, является ли пользователь администратором
        if user_id in admin_ids:
            # Если админ, перенаправляем в админ-панель
            await query.edit_message_text(
                "Добро пожаловать в панель администратора!",
                reply_markup=get_admin_main_keyboard()
            )
            return States.ADMIN_MAIN
        else:
            # Если обычный пользователь, показываем пользовательское меню
            welcome_message = get_message('welcome')
            await query.edit_message_text(
                welcome_message,
                reply_markup=get_user_main_keyboard()
            )
            return States.USER_WAITING_APPROVAL
    else:
        await query.answer("Неизвестная команда")
        return States.USER_CONFIRMATION

async def handle_add_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        channel_info = update.message.text.strip()
        
        # Здесь должна быть логика добавления канала
        # Для простоты просто сохраняем информацию
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Генерируем случайный ID канала (в реальности нужно получать его из Telegram API)
        channel_id = random.randint(100000, 999999)
        
        try:
            cursor.execute(
                'INSERT INTO channels (channel_id, title, username, invite_link) VALUES (?, ?, ?, ?)',
                (channel_id, f"Канал {channel_info}", channel_info, f"https://t.me/{channel_info}")
            )
            conn.commit()
            
            await update.message.reply_text(
                f"Канал {channel_info} добавлен!",
                reply_markup=get_admin_main_keyboard()
            )
        except sqlite3.IntegrityError:
            await update.message.reply_text(
                "Канал с таким ID уже существует.",
                reply_markup=get_admin_main_keyboard()
            )
        finally:
            conn.close()
        
        return States.ADMIN_MAIN

async def handle_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == 'add_channel':
        await query.edit_message_text(
            "Чтобы добавить канал, добавьте бота как администратора в нужный канал и отправьте мне @username канала или пригласительную ссылку.",
            reply_markup=get_back_keyboard()
        )
        return States.ADMIN_ADD_CHANNEL
    
    elif query.data.startswith('channel_'):
        channel_id = int(query.data.split('_')[1])
        context.user_data['current_channel'] = channel_id
        
        channel_title = None
        channels = get_channels()
        for cid, title in channels:
            if cid == channel_id:
                channel_title = title
                break
        
        await query.edit_message_text(
            f"Настройки канала: {channel_title}",
            reply_markup=get_channel_settings_keyboard(channel_id)
        )
        return States.ADMIN_CHANNEL_SETTINGS
    
    elif query.data == 'stats':
        # Общая статистика
        try:
            stats = get_general_stats()
            user_stats = get_user_stats()
            
            stats_text = f"""
📊 **Общая статистика бота**

👥 **Пользователи:**
• Всего пользователей: {stats['total_users']}
• Подтвержденных: {stats['confirmed_users']}
• За последние 7 дней: {user_stats['users_last_7_days']}
• За последние 30 дней: {user_stats['users_last_30_days']}

📺 **Каналы:**
• Всего каналов: {stats['total_channels']}

📝 **Заявки:**
• Всего заявок: {stats['total_applications']}
• Принято: {stats['approved_applications']}
• Ожидает: {stats['pending_applications']}

📢 **Рекламодатели:**
• Всего: {stats['total_advertisers']}
            """
            
            await query.edit_message_text(
                stats_text,
                parse_mode='Markdown',
                reply_markup=get_admin_main_keyboard()
            )
        except Exception as e:
            logger.error(f"Ошибка при получении статистики: {e}")
            await query.edit_message_text(
                "Ошибка при получении статистики.",
                reply_markup=get_admin_main_keyboard()
            )
        return States.ADMIN_MAIN
    
    elif query.data == 'broadcast':
        await query.edit_message_text(
            "Отправьте сообщение для рассылки всем пользователям бота:",
            reply_markup=get_back_keyboard()
        )
        return States.ADMIN_BROADCAST
    
    elif query.data == 'edit_text':
        # Клавиатура для редактирования текстов
        keyboard = [
            [InlineKeyboardButton("Приветственное сообщение", callback_data="edit_welcome")],
            [InlineKeyboardButton("Подтверждение человека", callback_data="edit_confirmation")],
            [InlineKeyboardButton("Реклама маленькая", callback_data="edit_ad_small")],
            [InlineKeyboardButton("Добавить сообщение", callback_data="add_message")],
            [InlineKeyboardButton("« Назад", callback_data="back_to_main")]
        ]
        
        await query.edit_message_text(
            "Выберите сообщение для редактирования:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return States.ADMIN_EDIT_TEXT
    
    elif query.data == 'add_advertiser':
        await query.edit_message_text(
            "Отправьте @username или пригласительную ссылку рекламного канала:",
            reply_markup=get_back_keyboard()
        )
        return States.ADMIN_ADD_ADVERTISER
    
    elif query.data == 'view_advertisers':
        try:
            advertisers = get_advertisers()
            
            if not advertisers:
                await query.edit_message_text(
                    "Рекламодатели не найдены.",
                    reply_markup=get_admin_main_keyboard()
                )
                return States.ADMIN_MAIN
            
            # Формируем список рекламодателей
            advertisers_text = "📢 **Список рекламодателей:**\n\n"
            
            keyboard = []
            for i, (advertiser_id, title, invite_link, priority) in enumerate(advertisers, 1):
                # Экранируем специальные символы для Markdown
                safe_title = escape_markdown(title)
                advertisers_text += f"{i}. **{safe_title}**\n"
                advertisers_text += f"   Ссылка: {invite_link}\n"
                advertisers_text += f"   Приоритет: {priority}\n\n"
                
                # Добавляем кнопку для удаления
                keyboard.append([InlineKeyboardButton(f"🗑️ Удалить {title}", callback_data=f"delete_advertiser_{advertiser_id}")])
            
            keyboard.append([InlineKeyboardButton("« Назад", callback_data="back_to_main")])
            
            await query.edit_message_text(
                advertisers_text,
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        except Exception as e:
            logger.error(f"Ошибка при получении рекламодателей: {e}")
            await query.edit_message_text(
                "Ошибка при получении списка рекламодателей.",
                reply_markup=get_admin_main_keyboard()
            )
        return States.ADMIN_MAIN
    
    elif query.data == 'messages':
        try:
            messages = get_user_messages(20)  # Получаем последние 20 сообщений
            
            if not messages:
                await query.edit_message_text(
                    "📭 Сообщений от пользователей пока нет.",
                    reply_markup=get_admin_main_keyboard()
                )
                return States.ADMIN_MAIN
            
            # Формируем список сообщений
            messages_text = "💬 **Сообщения от пользователей:**\n\n"
            
            keyboard = []
            unique_users = set()
            
            for msg in messages:
                msg_id, user_id, message_text, msg_type, created_at, username, first_name, last_name = msg
                
                if user_id not in unique_users:
                    unique_users.add(user_id)
                    user_name = f"{first_name} {last_name}" if first_name and last_name else (username or f"ID: {user_id}")
                    safe_user_name = escape_markdown(user_name)
                    safe_message = escape_markdown(message_text[:50] + "..." if len(message_text) > 50 else message_text)
                    
                    messages_text += f"👤 **{safe_user_name}**\n"
                    messages_text += f"📝 {safe_message}\n"
                    messages_text += f"🕒 {created_at}\n\n"
                    
                    # Добавляем кнопку для ответа
                    keyboard.append([InlineKeyboardButton(f"💬 Ответить {user_name}", callback_data=f"respond_to_{user_id}")])
            
            keyboard.append([InlineKeyboardButton("« Назад", callback_data="back_to_main")])
            
            await query.edit_message_text(
                messages_text,
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        except Exception as e:
            logger.error(f"Ошибка при получении сообщений: {e}")
            await query.edit_message_text(
                "Ошибка при получении сообщений.",
                reply_markup=get_admin_main_keyboard()
            )
        return States.ADMIN_MAIN
    
    elif query.data.startswith('respond_to_'):
        user_id = int(query.data.split('_')[2])
        context.user_data['responding_to_user'] = user_id
        
        # Получаем информацию о пользователе
        user = get_user(user_id)
        if user:
            _, username, first_name, last_name, _, _ = user
            user_name = f"{first_name} {last_name}" if first_name and last_name else (username or f"ID: {user_id}")
            
            await query.edit_message_text(
                f"💬 Ответ пользователю: {user_name}\n\nОтправьте ваше сообщение:",
                reply_markup=get_back_keyboard()
            )
            return States.ADMIN_RESPOND_TO_USER
        else:
            await query.edit_message_text(
                "Пользователь не найден.",
                reply_markup=get_admin_main_keyboard()
            )
            return States.ADMIN_MAIN
    
    elif query.data.startswith('delete_advertiser_'):
        advertiser_id = int(query.data.split('_')[2])
        try:
            if delete_advertiser(advertiser_id):
                await query.edit_message_text(
                    "Рекламодатель успешно удален!",
                    reply_markup=get_admin_main_keyboard()
                )
            else:
                await query.edit_message_text(
                    "Ошибка при удалении рекламодателя.",
                    reply_markup=get_admin_main_keyboard()
                )
        except Exception as e:
            logger.error(f"Ошибка при удалении рекламодателя: {e}")
            await query.edit_message_text(
                "Ошибка при удалении рекламодателя.",
                reply_markup=get_admin_main_keyboard()
            )
        return States.ADMIN_MAIN
    
    elif query.data == 'back_to_main':
        # Проверяем права администратора
        user_id = query.from_user.id
        try:
            with open('config.json', 'r', encoding='utf-8') as f:
                config = json.load(f)
            admin_ids = config.get('admin_ids', [])
        except:
            admin_ids = []
        
        if user_id not in admin_ids:
            # Если пользователь не админ, возвращаем его в пользовательское меню
            await query.edit_message_text(
                "Главное меню:",
                reply_markup=get_user_main_keyboard()
            )
            return States.USER_WAITING_APPROVAL
        else:
            # Если админ, показываем админ-панель
            await query.edit_message_text(
                "Панель администратора:",
                reply_markup=get_admin_main_keyboard()
            )
            return States.ADMIN_MAIN
    
    elif query.data == 'back_to_edit':
        # Клавиатура для редактирования текстов
        keyboard = [
            [InlineKeyboardButton("Приветственное сообщение", callback_data="edit_welcome")],
            [InlineKeyboardButton("Подтверждение человека", callback_data="edit_confirmation")],
            [InlineKeyboardButton("Реклама маленькая", callback_data="edit_ad_small")],
            [InlineKeyboardButton("Добавить сообщение", callback_data="add_message")],
            [InlineKeyboardButton("« Назад", callback_data="back_to_main")]
        ]
        
        await query.edit_message_text(
            "Выберите сообщение для редактирования:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return States.ADMIN_EDIT_TEXT
    
    elif query.data == 'back_to_user':
        await query.edit_message_text(
            "Главное меню:",
            reply_markup=get_user_main_keyboard()
        )
        return States.USER_WAITING_APPROVAL

async def handle_channel_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data.startswith('approval_'):
        channel_id = int(query.data.split('_')[1])
        context.user_data['current_channel'] = channel_id
        
        await query.edit_message_text(
            "Выберите время принятия заявки:",
            reply_markup=get_approval_settings_keyboard(channel_id)
        )
        return States.ADMIN_SET_APPROVAL_TIME
    
    elif query.data.startswith('stats_'):
        channel_id = int(query.data.split('_')[1])
        # Статистика для конкретного канала
        try:
            channel_stats = get_channel_stats(channel_id)
            
            if not channel_stats:
                await query.edit_message_text(
                    "Канал не найден.",
                    reply_markup=get_channel_settings_keyboard(channel_id)
                )
                return States.ADMIN_CHANNEL_SETTINGS
            
            # Формируем текст статистики
            # Экранируем специальные символы для Markdown
            title = escape_markdown(channel_stats['title'])
            username = escape_markdown(channel_stats['username']) if channel_stats['username'] else 'не указан'
            
            stats_text = f"""
📊 **Статистика канала: {title}**

📺 **Информация о канале:**
• Название: {title}
• Username: @{username}
• Автопринятие: {channel_stats['auto_approve_time']} мин

📝 **Заявки:**
• Всего заявок: {channel_stats['total_applications']}
• Принято: {channel_stats['approved_applications']}
• Ожидает: {channel_stats['pending_applications']}

👥 **Последние заявки:**
            """
            
            # Добавляем последние заявки
            if channel_stats['recent_applications']:
                for username, first_name, created_at, status in channel_stats['recent_applications']:
                    status_emoji = "✅" if status == "approved" else "⏳"
                    # Экранируем username и first_name
                    safe_username = escape_markdown(username) if username else None
                    safe_first_name = escape_markdown(first_name) if first_name else None
                    username_display = f"@{safe_username}" if safe_username else safe_first_name
                    stats_text += f"\n• {status_emoji} {username_display} ({created_at})"
            else:
                stats_text += "\n• Заявок пока нет"
            
            await query.edit_message_text(
                stats_text,
                parse_mode='Markdown',
                reply_markup=get_channel_settings_keyboard(channel_id)
            )
        except Exception as e:
            logger.error(f"Ошибка при получении статистики канала: {e}")
            await query.edit_message_text(
                "Ошибка при получении статистики канала.",
                reply_markup=get_channel_settings_keyboard(channel_id)
            )
        return States.ADMIN_CHANNEL_SETTINGS
    
    elif query.data == 'back_to_main':
        # Проверяем права администратора
        user_id = query.from_user.id
        try:
            with open('config.json', 'r', encoding='utf-8') as f:
                config = json.load(f)
            admin_ids = config.get('admin_ids', [])
        except:
            admin_ids = []
        
        if user_id not in admin_ids:
            # Если пользователь не админ, возвращаем его в пользовательское меню
            await query.edit_message_text(
                "Главное меню:",
                reply_markup=get_user_main_keyboard()
            )
            return States.USER_WAITING_APPROVAL
        else:
            # Если админ, показываем админ-панель
            await query.edit_message_text(
                "Панель администратора:",
                reply_markup=get_admin_main_keyboard()
            )
            return States.ADMIN_MAIN

async def handle_approval_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data.startswith('approve_'):
        parts = query.data.split('_')
        approval_type = parts[1]
        channel_id = int(parts[2])
        
        if approval_type == 'custom':
            await query.edit_message_text(
                "Укажите время в минуты:",
                reply_markup=get_back_to_approval_keyboard(channel_id)
            )
            return States.ADMIN_CUSTOM_TIME
        
        elif approval_type == 'none':
            update_channel_approval_time(channel_id, -1)
            await query.edit_message_text(
                "Заявки не будут приниматься автоматически.",
                reply_markup=get_approval_settings_keyboard(channel_id)
            )
        
        elif approval_type == 'all':
            # Принимаем все pending заявки
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute(
                'UPDATE applications SET status = "approved", processed_at = CURRENT_TIMESTAMP WHERE channel_id = ? AND status = "pending"',
                (channel_id,)
            )
            conn.commit()
            conn.close()
            
            await query.edit_message_text(
                "Все pending заявки приняты.",
                reply_markup=get_approval_settings_keyboard(channel_id)
            )
        
        else:
            minutes = int(approval_type)
            update_channel_approval_time(channel_id, minutes)
            await query.edit_message_text(
                f"Время автоматического принятия заявки установлено: {minutes} минут.",
                reply_markup=get_approval_settings_keyboard(channel_id)
            )
        
        return States.ADMIN_SET_APPROVAL_TIME
    
    elif query.data.startswith('back_to_channel_'):
        channel_id = int(query.data.split('_')[3])
        channel_title = None
        channels = get_channels()
        for cid, title in channels:
            if cid == channel_id:
                channel_title = title
                break
        
        await query.edit_message_text(
            f"Настройки канала: {channel_title}",
            reply_markup=get_channel_settings_keyboard(channel_id)
        )
        return States.ADMIN_CHANNEL_SETTINGS
    
    elif query.data.startswith('back_to_approval_'):
        channel_id = int(query.data.split('_')[3])
        await query.edit_message_text(
            "Выберите время принятия заявки:",
            reply_markup=get_approval_settings_keyboard(channel_id)
        )
        return States.ADMIN_SET_APPROVAL_TIME

async def handle_custom_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        try:
            minutes = int(update.message.text)
            channel_id = context.user_data.get('current_channel')
            
            if channel_id:
                update_channel_approval_time(channel_id, minutes)
                await update.message.reply_text(
                    f"Время автоматического принятия заявки установлено: {minutes} минут.",
                    reply_markup=get_approval_settings_keyboard(channel_id)
                )
                return States.ADMIN_SET_APPROVAL_TIME
        except ValueError:
            await update.message.reply_text("Пожалуйста, введите число (минуты):", reply_markup=get_back_keyboard())
            return States.ADMIN_CUSTOM_TIME
    
    elif update.callback_query:
        query = update.callback_query
        await query.answer()
        
        if query.data.startswith('back_to_approval_'):
            channel_id = int(query.data.split('_')[3])
            await query.edit_message_text(
                "Выберите время принятия заявки:",
                reply_markup=get_approval_settings_keyboard(channel_id)
            )
            return States.ADMIN_SET_APPROVAL_TIME

async def handle_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        # Получаем всех пользователей
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute('SELECT user_id FROM users')
            users = cursor.fetchall()
            
            # Отправляем сообщение всем пользователям
            success_count = 0
            error_count = 0
            
            for user_id, in users:
                try:
                    if update.message.text:
                        await context.bot.send_message(user_id, update.message.text)
                    elif update.message.photo:
                        await context.bot.send_photo(
                            user_id, 
                            update.message.photo[-1].file_id, 
                            caption=update.message.caption
                        )
                    elif update.message.video:
                        await context.bot.send_video(
                            user_id, 
                            update.message.video.file_id, 
                            caption=update.message.caption
                        )
                    # Добавьте другие типы медиа по необходимости
                    success_count += 1
                except Exception as e:
                    logger.error(f"Ошибка при отправке сообщения пользователю {user_id}: {e}")
                    error_count += 1
            
            await update.message.reply_text(
                f"Рассылка завершена! Отправлено: {success_count}, Ошибок: {error_count}",
                reply_markup=get_admin_main_keyboard()
            )
        except Exception as e:
            logger.error(f"Ошибка при получении списка пользователей: {e}")
            await update.message.reply_text(
                "Ошибка при получении списка пользователей.",
                reply_markup=get_admin_main_keyboard()
            )
        finally:
            conn.close()
        
        return States.ADMIN_MAIN

async def handle_edit_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == 'back_to_main':
        await query.edit_message_text(
            "Панель администратора:",
            reply_markup=get_admin_main_keyboard()
        )
        return States.ADMIN_MAIN
    
    elif query.data.startswith('edit_'):
        message_type = query.data[5:]  # Убираем "edit_"
        context.user_data['editing_message'] = message_type
        
        current_text = get_message(message_type)
        
        await query.edit_message_text(
            f"Текущий текст: {current_text}\n\nОтправьте новый текст:",
            reply_markup=get_back_to_edit_keyboard()
        )
        return States.ADMIN_EDIT_TEXT
    
    elif query.data == 'add_message':
        await query.edit_message_text(
            "Введите название нового сообщения:",
            reply_markup=get_back_to_edit_keyboard()
        )
        return States.ADMIN_ADD_MESSAGE
    
    elif query.data == 'back_to_edit':
        # Клавиатура для редактирования текстов
        keyboard = [
            [InlineKeyboardButton("Приветственное сообщение", callback_data="edit_welcome")],
            [InlineKeyboardButton("Подтверждение человека", callback_data="edit_confirmation")],
            [InlineKeyboardButton("Реклама маленькая", callback_data="edit_ad_small")],
            [InlineKeyboardButton("Добавить сообщение", callback_data="add_message")],
            [InlineKeyboardButton("« Назад", callback_data="back_to_main")]
        ]
        
        await query.edit_message_text(
            "Выберите сообщение для редактирования:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return States.ADMIN_EDIT_TEXT

async def handle_add_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        message_name = update.message.text.strip()
        
        # Добавляем новое сообщение в базу
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                'INSERT INTO bot_messages (name, text) VALUES (?, ?)',
                (message_name, "Новое сообщение")
            )
            conn.commit()
            
            await update.message.reply_text(
                f"Сообщение '{message_name}' добавлено. Теперь вы можете отредактировать его текст.",
                reply_markup=get_back_to_edit_keyboard()
            )
        except sqlite3.IntegrityError:
            await update.message.reply_text(
                "Сообщение с таким названием уже существует.",
                reply_markup=get_back_to_edit_keyboard()
            )
        finally:
            conn.close()
        
        return States.ADMIN_EDIT_TEXT

async def handle_save_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        message_type = context.user_data.get('editing_message')
        new_text = update.message.text
        
        if message_type:
            update_message(message_type, new_text)
            
            await update.message.reply_text(
                "Текст успешно обновлен!",
                reply_markup=get_admin_main_keyboard()
            )
            return States.ADMIN_MAIN

async def handle_add_advertiser(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        advertiser_link = update.message.text.strip()
        
        # Здесь должна быть логика добавления рекламодателя в базу
        # Для простоты просто сохраняем ссылку
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                'INSERT INTO advertisers (title, invite_link) VALUES (?, ?)',
                ("Рекламный канал", advertiser_link)
            )
            conn.commit()
            
            await update.message.reply_text(
                "Рекламодатель добавлен!",
                reply_markup=get_admin_main_keyboard()
            )
        except sqlite3.IntegrityError:
            await update.message.reply_text(
                "Рекламодатель с такой ссылкой уже существует.",
                reply_markup=get_admin_main_keyboard()
            )
        except Exception as e:
            logger.error(f"Ошибка при добавлении рекламодателя: {e}")
            await update.message.reply_text(
                "Ошибка при добавлении рекламодателя.",
                reply_markup=get_admin_main_keyboard()
            )
        finally:
            conn.close()
        
        return States.ADMIN_MAIN

async def handle_user_contact_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == 'contact_admin':
        await query.edit_message_text(
            "Отправьте ваше сообщение администратору:",
            reply_markup=get_back_to_user_keyboard()
        )
        return States.USER_CONTACT_ADMIN

async def handle_user_message_to_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        user_id = update.message.from_user.id
        message_text = update.message.text
        
        try:
            # Сохраняем сообщение в базу данных
            add_user_message(user_id, message_text, 'user_to_admin')
            
            await update.message.reply_text(
                "✅ Ваше сообщение отправлено администратору. Мы ответим вам в ближайшее время.",
                reply_markup=get_user_main_keyboard()
            )
        except Exception as e:
            logger.error(f"Ошибка при сохранении сообщения пользователя: {e}")
            await update.message.reply_text(
                "❌ Ошибка при отправке сообщения. Попробуйте позже.",
                reply_markup=get_user_main_keyboard()
            )
        
        return States.USER_WAITING_APPROVAL

async def handle_admin_response_to_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик ответа админа пользователю"""
    if update.message:
        admin_id = update.message.from_user.id
        response_text = update.message.text
        target_user_id = context.user_data.get('responding_to_user')
        
        if not target_user_id:
            await update.message.reply_text(
                "❌ Ошибка: не указан получатель сообщения.",
                reply_markup=get_admin_main_keyboard()
            )
            return States.ADMIN_MAIN
        
        try:
            # Отправляем сообщение пользователю
            await context.bot.send_message(
                chat_id=target_user_id,
                text=f"💬 **Ответ от администратора:**\n\n{response_text}"
            )
            
            # Очищаем все сообщения пользователя после ответа
            clear_user_messages(target_user_id)
            
            # Получаем информацию о пользователе для подтверждения
            user = get_user(target_user_id)
            if user:
                _, username, first_name, last_name, _, _ = user
                user_name = f"{first_name} {last_name}" if first_name and last_name else (username or f"ID: {target_user_id}")
                
                await update.message.reply_text(
                    f"✅ Ответ отправлен пользователю: {user_name}\n🗑️ Сообщения пользователя очищены",
                    reply_markup=get_admin_main_keyboard()
                )
            else:
                await update.message.reply_text(
                    f"✅ Ответ отправлен пользователю ID: {target_user_id}\n🗑️ Сообщения пользователя очищены",
                    reply_markup=get_admin_main_keyboard()
                )
            
            # Очищаем данные о получателе
            context.user_data.pop('responding_to_user', None)
            
        except Exception as e:
            logger.error(f"Ошибка при отправке ответа пользователю: {e}")
            await update.message.reply_text(
                "❌ Ошибка при отправке ответа. Пользователь может быть заблокирован или удален.",
                reply_markup=get_admin_main_keyboard()
            )
        
        return States.ADMIN_MAIN

async def handle_try_luck(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    try:
        # Простая реализация рулетки 777
        symbols = ["🍒", "🍋", "🍊", "🍇", "🔔", "💎", "7️⃣"]
        result = [random.choice(symbols) for _ in range(3)]
        
        # Проверяем выигрышные комбинации
        if result[0] == result[1] == result[2] == "7️⃣":
            prize = "ДЖЕКПОТ! 🎉"
        elif result[0] == result[1] == result[2]:
            prize = "Большой приз! 🎊"
        elif result[0] == result[1] or result[1] == result[2]:
            prize = "Малый приз! 👍"
        else:
            prize = "Повезет в следующий раз! 😉"
        
        await query.edit_message_text(
            f"🎰 Результат: {' '.join(result)}\n\n{prize}",
            reply_markup=get_user_main_keyboard()
        )
    except Exception as e:
        logger.error(f"Ошибка в игре удачи: {e}")
        await query.edit_message_text(
            "Ошибка в игре. Попробуйте позже.",
            reply_markup=get_user_main_keyboard()
        )
    
    return States.USER_WAITING_APPROVAL

async def handle_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    try:
        user_id = query.from_user.id
        user = get_user(user_id)
        
        if user:
            user_id, username, first_name, last_name, registered_at, is_confirmed = user
            
            profile_text = f"""
👤 Ваш профиль:

ID: {user_id}
Имя: {first_name} {last_name}
Юзернейм: @{username if username else 'не указан'}
Дата регистрации: {registered_at}
Статус: {'Подтвержден' if is_confirmed else 'Не подтвержден'}
            """
            
            await query.edit_message_text(
                profile_text,
                reply_markup=get_user_main_keyboard()
            )
        else:
            await query.edit_message_text(
                "Профиль не найден. Попробуйте перезапустить бота командой /start",
                reply_markup=get_user_main_keyboard()
            )
    except Exception as e:
        logger.error(f"Ошибка при получении профиля: {e}")
        await query.edit_message_text(
            "Ошибка при получении профиля. Попробуйте позже.",
            reply_markup=get_user_main_keyboard()
        )
    
    return States.USER_WAITING_APPROVAL

async def handle_user_back_to_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик кнопки 'Назад' для обычных пользователей"""
    query = update.callback_query
    await query.answer()
    
    try:
        # Загружаем конфигурацию для проверки прав
        with open('config.json', 'r', encoding='utf-8') as f:
            config = json.load(f)
        admin_ids = config.get('admin_ids', [])
        
        user_id = query.from_user.id
        
        # Проверяем, является ли пользователь администратором
        if user_id in admin_ids:
            # Если админ, перенаправляем в админ-панель
            try:
                await query.edit_message_text(
                    "Добро пожаловать в панель администратора!",
                    reply_markup=get_admin_main_keyboard()
                )
            except Exception:
                # Если не удалось изменить сообщение, отправляем новое
                await query.message.reply_text(
                    "Добро пожаловать в панель администратора!",
                    reply_markup=get_admin_main_keyboard()
                )
            return States.ADMIN_MAIN
        else:
            # Если обычный пользователь, показываем пользовательское меню
            welcome_message = get_message('welcome')
            try:
                await query.edit_message_text(
                    welcome_message,
                    reply_markup=get_user_main_keyboard()
                )
            except Exception:
                # Если не удалось изменить сообщение, отправляем новое
                await query.message.reply_text(
                    welcome_message,
                    reply_markup=get_user_main_keyboard()
                )
            return States.USER_WAITING_APPROVAL
            
    except Exception as e:
        logger.error(f"Ошибка при обработке кнопки 'Назад': {e}")
        # В случае ошибки показываем пользовательское меню
        welcome_message = get_message('welcome')
        try:
            await query.edit_message_text(
                welcome_message,
                reply_markup=get_user_main_keyboard()
            )
        except Exception:
            await query.message.reply_text(
                welcome_message,
                reply_markup=get_user_main_keyboard()
            )
        return States.USER_WAITING_APPROVAL

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Операция отменена.",
        reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(msg="Exception while handling an update:", exc_info=context.error)
    
    # Уведомляем пользователя об ошибке
    if update and update.effective_message:
        await update.effective_message.reply_text(
            "Произошла ошибка. Пожалуйста, попробуйте позже."
        )

def main():
    # Инициализация базы данных
    init_db()
    
    # Загрузка конфигурации
    try:
        with open('config.json', 'r', encoding='utf-8') as f:
            config = json.load(f)
    except FileNotFoundError:
        logger.error("Файл config.json не найден. Создайте его с токеном бота и ID администраторов.")
        return
    except json.JSONDecodeError as e:
        logger.error(f"Ошибка в формате config.json: {e}")
        return
    
    # Проверяем наличие обязательных полей
    if 'bot_token' not in config:
        logger.error("В config.json отсутствует поле 'bot_token'")
        return
    if 'admin_ids' not in config:
        logger.error("В config.json отсутствует поле 'admin_ids'")
        return
    
    # Создание приложения
    try:
        application = Application.builder().token(config['bot_token']).build()
    except Exception as e:
        logger.error(f"Ошибка создания приложения: {e}")
        return
    
    # Сохраняем ID админов
    application.bot_data['admin_ids'] = config['admin_ids']
    
    # Создаем планировщик для автоматического принятия заявок
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        auto_approve_applications,
        trigger=IntervalTrigger(minutes=1),  # Проверяем каждую минуту
        id='auto_approve_job',
        name='Автоматическое принятие заявок',
        replace_existing=True
    )
    scheduler.start()
    logger.info("Планировщик автоматического принятия заявок запущен")
    
    # Создаем единый обработчик диалога
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            States.ADMIN_MAIN: [CallbackQueryHandler(handle_admin_panel)],
            States.ADMIN_ADD_CHANNEL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_add_channel),
                CallbackQueryHandler(handle_admin_panel, pattern='^back_to_main$')
            ],
            States.ADMIN_CHANNEL_SETTINGS: [CallbackQueryHandler(handle_channel_settings)],
            States.ADMIN_SET_APPROVAL_TIME: [CallbackQueryHandler(handle_approval_settings)],
            States.ADMIN_CUSTOM_TIME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_custom_time),
                CallbackQueryHandler(handle_approval_settings)
            ],
            States.ADMIN_BROADCAST: [
                MessageHandler(filters.TEXT | filters.PHOTO | filters.VIDEO, handle_broadcast),
                CallbackQueryHandler(handle_admin_panel, pattern='^back_to_main$')
            ],
            States.ADMIN_EDIT_TEXT: [
                CallbackQueryHandler(handle_edit_text),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_save_text)
            ],
            States.ADMIN_ADD_MESSAGE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_add_message),
                CallbackQueryHandler(handle_edit_text, pattern='^back_to_edit$')
            ],
            States.ADMIN_ADD_ADVERTISER: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_add_advertiser),
                CallbackQueryHandler(handle_admin_panel, pattern='^back_to_main$')
            ],
            States.USER_CONFIRMATION: [CallbackQueryHandler(handle_user_confirmation)],
            States.USER_WAITING_APPROVAL: [
                CallbackQueryHandler(handle_user_contact_admin, pattern='^contact_admin$'),
                CallbackQueryHandler(handle_try_luck, pattern='^try_luck$'),
                CallbackQueryHandler(handle_profile, pattern='^profile$'),
                CallbackQueryHandler(handle_user_back_to_main, pattern='^back_to_main$')
            ],
            States.USER_CONTACT_ADMIN: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_user_message_to_admin),
                CallbackQueryHandler(handle_user_back_to_main, pattern='^back_to_user$')
            ],
            States.ADMIN_RESPOND_TO_USER: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_admin_response_to_user),
                CallbackQueryHandler(handle_admin_panel, pattern='^back_to_main$')
            ],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
        per_message=False,
        per_chat=True,
        per_user=True
    )
    
    # Добавляем обработчик в приложение
    application.add_handler(conv_handler)
    
    # Добавляем обработчик ошибок
    application.add_error_handler(error_handler)
    
    # Запускаем бота
    logger.info("Запуск бота...")
    try:
        application.run_polling(allowed_updates=Update.ALL_TYPES)
    except Exception as e:
        logger.error(f"Ошибка при запуске бота: {e}")

if __name__ == '__main__':
    main()