# managers.py
from django.db import models
from django.utils import timezone
from telegram import Update
import logging

logger = logging.getLogger(__name__)

class TelegramUserManager(models.Manager):
    
    def get_or_create_user(self, update: Update):
        """Создает или получает пользователя"""
        try:
            user_data = update.effective_user
            
            user, created = self.get_or_create(
                user_id=user_data.id,
                defaults={
                    'username': user_data.username,
                    'first_name': user_data.first_name or '',
                    'last_name': user_data.last_name or '',
                    'language_code': user_data.language_code,
                }
            )
            
            return user, created
        
        except Exception as e:
            logger.error(f"Ошибка в get_or_create_user: {e}")
            # Создаем минимальную запись пользователя
            try:
                user = self.create(
                    user_id=user_data.id,
                    username=user_data.username or f"user_{user_data.id}",
                    first_name=user_data.first_name or "User",
                    last_name=user_data.last_name or "",
                    language_code=user_data.language_code or "ru",
                )
                return user, True
            except Exception as create_error:
                logger.error(f"Не удалось создать пользователя: {create_error}")
                return None, False
    
    def update_user_activity(self, user_id: int):
        """Обновляет время последней активности"""
        self.filter(user_id=user_id).update(last_activity=timezone.now())