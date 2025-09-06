# middleware.py
from telegram import Update
from telegram.ext import ContextTypes, Application  # Добавляем импорт Application
from .user_service import UserService

class UserActivityMiddleware:
    
    async def pre_process(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка перед выполнением команды"""
        if update.effective_user:
            try:
                # Создаем/обновляем пользователя
                user, created = await UserService.get_or_create_telegram_user(update)
                context.user_data['telegram_user'] = user
                context.user_data['user_id'] = user.user_id
                
                # Обновляем активность
                await UserService.update_user_activity(user.user_id)
                
            except Exception as e:
                print(f"Ошибка в middleware: {e}")
    
    async def post_process(self, update: Update, context: ContextTypes.DEFAULT_TYPE, exception: Exception = None):
        """Обработка после выполнения команды"""
        pass

