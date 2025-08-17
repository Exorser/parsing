import os
import django
import sys
from pathlib import Path

# Добавляем путь к проекту
BASE_DIR = Path(__file__).parent.parent
sys.path.append(str(BASE_DIR))

# Настраиваем Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'project.project.settings')
django.setup()  # Это должно быть ПЕРЕД импортом моделей

from bot.bot import WBBot

if __name__ == "__main__":
    bot = WBBot(os.getenv("TELEGRAM_BOT_TOKEN"))
    bot.run()