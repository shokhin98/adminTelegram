#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Универсальный скрипт запуска Telegram бота
Работает на Windows, Linux, macOS
"""

import sys
import os
import subprocess
import platform

def check_python_version():
    """Проверяет версию Python"""
    if sys.version_info < (3, 7):
        print("❌ Требуется Python 3.7 или выше")
        print(f"Текущая версия: {sys.version}")
        return False
    print(f"✓ Python версия: {sys.version}")
    return True

def check_dependencies():
    """Проверяет наличие необходимых зависимостей"""
    required_packages = [
        'telegram',
        'sqlite3',
        'pytz'
    ]
    
    missing_packages = []
    
    for package in required_packages:
        try:
            if package == 'sqlite3':
                import sqlite3
            elif package == 'telegram':
                import telegram
            elif package == 'pytz':
                import pytz
            print(f"✓ {package} установлен")
        except ImportError:
            missing_packages.append(package)
            print(f"❌ {package} не найден")
    
    if missing_packages:
        print(f"\n📦 Установите недостающие пакеты:")
        print(f"pip install {' '.join(missing_packages)}")
        return False
    
    return True

def check_config():
    """Проверяет наличие и корректность конфигурации"""
    if not os.path.exists('config.json'):
        print("❌ Файл config.json не найден")
        print("Создайте файл config.json с токеном бота и ID администраторов")
        return False
    
    try:
        import json
        with open('config.json', 'r', encoding='utf-8') as f:
            config = json.load(f)
        
        if 'bot_token' not in config:
            print("❌ В config.json отсутствует поле 'bot_token'")
            return False
        
        if 'admin_ids' not in config:
            print("❌ В config.json отсутствует поле 'admin_ids'")
            return False
        
        print("✓ Конфигурация корректна")
        return True
        
    except json.JSONDecodeError as e:
        print(f"❌ Ошибка в формате config.json: {e}")
        return False
    except Exception as e:
        print(f"❌ Ошибка при чтении config.json: {e}")
        return False

def check_database():
    """Проверяет работу базы данных"""
    try:
        from main import init_db, get_db_connection
        init_db()
        
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT name FROM sqlite_master WHERE type="table"')
        tables = cursor.fetchall()
        conn.close()
        
        print(f"✓ База данных работает. Таблиц: {len(tables)}")
        return True
        
    except Exception as e:
        print(f"❌ Ошибка базы данных: {e}")
        return False

def start_bot():
    """Запускает бота"""
    try:
        print("\n🚀 Запуск бота...")
        print("=" * 50)
        
        # Импортируем и запускаем main
        from main import main
        main()
        
    except KeyboardInterrupt:
        print("\n\n⏹️  Бот остановлен пользователем")
    except Exception as e:
        print(f"\n❌ Ошибка при запуске бота: {e}")
        import traceback
        traceback.print_exc()

def main():
    """Основная функция"""
    print("🤖 Telegram Bot Launcher")
    print("=" * 50)
    print(f"ОС: {platform.system()} {platform.release()}")
    print(f"Архитектура: {platform.machine()}")
    print("=" * 50)
    
    # Проверяем все требования
    checks = [
        ("Версия Python", check_python_version),
        ("Зависимости", check_dependencies),
        ("Конфигурация", check_config),
        ("База данных", check_database)
    ]
    
    for check_name, check_func in checks:
        print(f"\n🔍 Проверка: {check_name}")
        if not check_func():
            print(f"\n❌ Проверка '{check_name}' не пройдена")
            print("Исправьте ошибки и попробуйте снова")
            return False
    
    print("\n✅ Все проверки пройдены!")
    
    # Запускаем бота
    start_bot()
    
    return True

if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print(f"❌ Критическая ошибка: {e}")
        sys.exit(1)