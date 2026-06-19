#!/bin/bash
# Универсальный скрипт запуска Telegram бота для Unix-систем (Linux, macOS)

echo "🤖 Telegram Bot Launcher"
echo "=================================================="
echo "ОС: $(uname -s) $(uname -r)"
echo "Архитектура: $(uname -m)"
echo "=================================================="

# Проверяем наличие Python
if ! command -v python3 &> /dev/null; then
    echo "❌ Python3 не найден. Установите Python 3.7 или выше"
    exit 1
fi

# Проверяем версию Python
python_version=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "✓ Python версия: $python_version"

# Проверяем наличие pip
if ! command -v pip3 &> /dev/null; then
    echo "❌ pip3 не найден. Установите pip"
    exit 1
fi

# Проверяем зависимости
echo ""
echo "🔍 Проверка зависимостей..."
python3 -c "
import sys
required_packages = ['telegram', 'pytz']
missing = []

for package in required_packages:
    try:
        __import__(package)
        print(f'✓ {package} установлен')
    except ImportError:
        missing.append(package)
        print(f'❌ {package} не найден')

if missing:
    print(f'\\n📦 Установите недостающие пакеты:')
    print(f'pip3 install {\" \".join(missing)}')
    sys.exit(1)
"

if [ $? -ne 0 ]; then
    exit 1
fi

# Проверяем конфигурацию
echo ""
echo "🔍 Проверка конфигурации..."
if [ ! -f "config.json" ]; then
    echo "❌ Файл config.json не найден"
    echo "Создайте файл config.json с токеном бота и ID администраторов"
    exit 1
fi

python3 -c "
import json
try:
    with open('config.json', 'r', encoding='utf-8') as f:
        config = json.load(f)
    
    if 'bot_token' not in config:
        print('❌ В config.json отсутствует поле bot_token')
        exit(1)
    
    if 'admin_ids' not in config:
        print('❌ В config.json отсутствует поле admin_ids')
        exit(1)
    
    print('✓ Конфигурация корректна')
except Exception as e:
    print(f'❌ Ошибка в config.json: {e}')
    exit(1)
"

if [ $? -ne 0 ]; then
    exit 1
fi

# Проверяем базу данных
echo ""
echo "🔍 Проверка базы данных..."
python3 -c "
try:
    from main import init_db, get_db_connection
    init_db()
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT name FROM sqlite_master WHERE type=\"table\"')
    tables = cursor.fetchall()
    conn.close()
    
    print(f'✓ База данных работает. Таблиц: {len(tables)}')
except Exception as e:
    print(f'❌ Ошибка базы данных: {e}')
    exit(1)
"

if [ $? -ne 0 ]; then
    exit 1
fi

echo ""
echo "✅ Все проверки пройдены!"
echo ""
echo "🚀 Запуск бота..."
echo "=================================================="

# Запускаем бота
python3 main.py