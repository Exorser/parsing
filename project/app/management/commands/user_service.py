# services/user_service.py
import logging
from django.utils import timezone
from typing import Dict, Any
from telegram import Update
from app.models import TelegramUser, UserSearchHistory

logger = logging.getLogger(__name__)

class UserService:
    
    @staticmethod
    async def get_or_create_telegram_user(update: Update):
        """Простое создание/получение пользователя"""
        from asgiref.sync import sync_to_async
        
        try:
            if not update or not update.effective_user:
                logger.error("Неверный объект update или effective_user")
                return None, False
                
            user_data = update.effective_user
            
            # Проверяем, что есть необходимые данные
            if not hasattr(user_data, 'id'):
                logger.error("У effective_user нет атрибута id")
                return None, False
            
            user, created = await sync_to_async(
                lambda: TelegramUser.objects.get_or_create_user(update)
            )()
            return user, created
        except Exception as e:
            logger.error(f"Ошибка создания пользователя: {e}")
            return None, False
    
    @staticmethod
    async def update_user_activity(user_id: int):
        """Обновляет активность пользователя"""
        from asgiref.sync import sync_to_async
        
        try:
            await sync_to_async(
                lambda: TelegramUser.objects.filter(user_id=user_id).update(
                    last_activity=timezone.now()
                )
            )()
        except Exception as e:
            logger.error(f"Ошибка обновления активности: {e}")
    
    @staticmethod
    async def increment_search_count(user_id: int):
        """Увеличивает счетчик поисков"""
        from asgiref.sync import sync_to_async
        from django.db.models import F
        
        try:
            await sync_to_async(
                lambda: TelegramUser.objects.filter(user_id=user_id).update(
                    search_count=F('search_count') + 1,
                    last_activity=timezone.now()
                )
            )()
        except Exception as e:
            logger.debug(f"Не удалось увеличить счетчик: {e}")
    
    @staticmethod
    async def save_search_history(user_id: int, query: str, platform: str, results_count: int):
        """Сохраняет историю поиска с созданием пользователя если нужно"""
        from asgiref.sync import sync_to_async
        from app.models import TelegramUser, UserSearchHistory
        
        try:
            # Пытаемся найти пользователя
            try:
                user = await sync_to_async(TelegramUser.objects.get)(user_id=user_id)
            except TelegramUser.DoesNotExist:
                # Если пользователь не существует, создаем базовую запись
                user = await sync_to_async(TelegramUser.objects.create)(
                    user_id=user_id,
                    username=f"user_{user_id}",
                    first_name="Unknown",
                    last_name="User",
                    search_count=0,
                    products_viewed=0,
                    alerts_created=0
                )
                logger.info(f"Создан новый пользователь для истории поиска: {user_id}")
            
            # Сохраняем историю поиска
            await sync_to_async(UserSearchHistory.objects.create)(
                user=user,
                query=query,
                platform=platform,
                results_count=results_count
            )
            
        except Exception as e:
            logger.error(f"Ошибка сохранения истории поиска: {e}")
    
    @staticmethod
    async def get_user_stats(user_id: int) -> Dict[str, Any]:
        """Получает статистику пользователя"""
        from asgiref.sync import sync_to_async
        
        try:
            user = await sync_to_async(TelegramUser.objects.get)(user_id=user_id)
            
            return {
                'user_id': user.user_id,
                'username': user.username,
                'first_name': user.first_name,
                'created_at': user.created_at,
                'search_count': user.search_count,
                'products_viewed': user.products_viewed,
                'alerts_created': user.alerts_created,
                'last_activity': user.last_activity,
            }
        except Exception as e:
            logger.error(f"Ошибка получения статистики: {e}")
            return {}