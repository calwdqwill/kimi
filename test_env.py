"""
Диагностика .env файла.
Запустите: py test_env.py
"""
from pathlib import Path
from dotenv import load_dotenv
import os

backend_env = Path(__file__).resolve().parent / ".env"
parent_env = Path(__file__).resolve().parent.parent / ".env"

print("=" * 60)
print("ДИАГНОСТИКА .env")
print("=" * 60)

print(f"\n1. Проверяем файл backend/.env: {backend_env}")
print(f"   Существует: {backend_env.exists()}")

print(f"\n2. Проверяем файл ../.env: {parent_env}")
print(f"   Существует: {parent_env.exists()}")

env_to_load = None
if backend_env.exists():
    env_to_load = backend_env
elif parent_env.exists():
    env_to_load = parent_env

if env_to_load:
    print(f"\n3. Загружаем .env из: {env_to_load}")
    
    # Сырой просмотр файла
    with open(env_to_load, 'rb') as f:
        raw = f.read()
    print(f"   Размер файла: {len(raw)} bytes")
    print(f"   Первые 100 bytes: {raw[:100]}")
    
    load_dotenv(dotenv_path=env_to_load)
    
    token = os.getenv("FINAM_TOKEN", "").strip()
    client_id = os.getenv("FINAM_CLIENT_ID", "").strip()
    
    print(f"\n4. Результат:")
    print(f"   FINAM_TOKEN: {'ЗАГРУЖЕН' if token else 'ПУСТОЙ'} (длина: {len(token)})")
    if token:
        print(f"   Начало: {token[:40]}...")
        print(f"   Конец: ...{token[-30:]}")
    print(f"   FINAM_CLIENT_ID: {client_id if client_id else 'не задан'}")
else:
    print("\n   ❌ .env НЕ НАЙДЕН!")
    print("   Создайте файл .env в папке backend/ с содержимым:")
    print("   FINAM_TOKEN=ваш_токен")

print("\n" + "=" * 60)
