import asyncio
import logging
import requests
from typing import List, Optional, Tuple
from django.core.management.base import BaseCommand
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler
)
from app.base_parser import WildberriesParser, OzonParser
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import aiohttp
from datetime import datetime
import sys
from logging.handlers import RotatingFileHandler
from .user_service import UserService

class IgnoreUnicodeErrorsHandler(logging.StreamHandler):
    def emit(self, record):
        try:
            super().emit(record)
        except UnicodeEncodeError:
            pass

def setup_logging():
    """Настройка комплексного логирования"""
    # Создаем форматтер
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # Настройка корневого логгера
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    
    # Удаляем существующие обработчики
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    
    # Консольный вывод
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.INFO)
    
    # Файловый вывод с ротацией
    file_handler = RotatingFileHandler(
        'telegram_bot.log',
        maxBytes=10*1024*1024,  # 10 MB
        backupCount=5,
        encoding='utf-8'
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.DEBUG)
    
    # Обработчик для ошибок
    error_handler = logging.FileHandler('telegram_errors.log', encoding='utf-8')
    error_handler.setFormatter(formatter)
    error_handler.setLevel(logging.ERROR)
    
    # Добавляем обработчики
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(error_handler)
    
    # Устанавливаем уровень для specific логгеров
    logging.getLogger('telegram').setLevel(logging.WARNING)
    logging.getLogger('apscheduler').setLevel(logging.WARNING)
    logging.getLogger('urllib3').setLevel(logging.WARNING)
    
    return root_logger

setup_logging()
# Настройка логгера
logger = logging.getLogger(__name__)
logger.addHandler(IgnoreUnicodeErrorsHandler())
logger.setLevel(logging.INFO)

# Состояния для ConversationHandler
SEARCH_QUERY, SEARCH_PLATFORM, SEARCH_LIMIT = range(3)

class MultiPlatformBot:
    def __init__(self, token: str):
        self.token = token
        self.parsers = {
            'WB': WildberriesParser(),
            'OZ': OzonParser()
        }
        self.current_parser = self.parsers['WB']  # По умолчанию Wildberries
        self.executor = ThreadPoolExecutor(max_workers=5)
        self.session = None
        self.current_search_task = None  # Текущая задача поиска
        self.search_lock = asyncio.Lock()
        self.user_service = UserService()
    
    async def init_session(self):
        """Инициализация сессии в асинхронном контексте"""
        if self.session is None:
            self.session = aiohttp.ClientSession()
            
        if hasattr(self.parsers['OZ'], 'init_session_async'):
            await self.parsers['OZ'].init_session_async()
    
    async def close_session(self):
        """Закрытие сессии и отмена всех задач"""
        try:
            # Отменяем текущий поиск
            await self.cancel_current_search()
            
            # Закрываем сессии парсеров
            for parser in self.parsers.values():
                if hasattr(parser, 'close_session'):
                    await parser.close_session()
                elif hasattr(parser, 'session'):
                    parser.session.close()
            
            # Закрываем основную сессию
            if self.session:
                await self.session.close()
                self.session = None
                
            # Закрываем executor
            self.executor.shutdown(wait=False)
            
        except Exception as e:
            logger.error(f"Ошибка при закрытии сессии: {e}")
        finally:
            # Гарантируем очистку
            self.session = None
            async with self.search_lock:
                self.current_search_task = None

    def _get_main_keyboard(self):
        """Основная клавиатура с кнопками"""
        keyboard = [
            [KeyboardButton("🔍 Поиск товаров"), KeyboardButton("🛒 Сменить платформу")],
            [KeyboardButton("🔄 История поиска"), KeyboardButton("ℹ️ Помощь")],
            [KeyboardButton("🎯 Топ товаров"), KeyboardButton("💎 Акции")]
        ]
        return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, input_field_placeholder="Выберите действие...")

    def _get_platform_keyboard(self):
        """Клавиатура для выбора платформы"""
        keyboard = [
            [KeyboardButton("Wildberries 🛍️"), KeyboardButton("Ozon 🟠")],
            [KeyboardButton("↩️ Назад в меню")]
        ]
        return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

    def _get_search_keyboard(self):
        """Клавиатура для поиска"""
        keyboard = [
            [KeyboardButton("5 товаров"), KeyboardButton("10 товаров")],
            [KeyboardButton("15 товаров"), KeyboardButton("20 товаров")],
            [KeyboardButton("↩️ Назад в меню")]
        ]
        return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, input_field_placeholder="Выберите количество...")

    def _get_cancel_keyboard(self):
        """Клавиатура только с кнопкой отмены поиска"""
        keyboard = [
            [KeyboardButton("❌ Отменить поиск")]
        ]
        return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, input_field_placeholder="Поиск выполняется...")

    def _get_quick_search_keyboard(self):
        """Клавиатура для быстрого поиска с кнопкой отмены"""
        keyboard = [
            [KeyboardButton("❌ Отменить поиск")]
        ]
        return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, input_field_placeholder="Идет поиск...")
    
    async def cancel_current_search(self):
        """Мгновенная отмена текущего поиска"""
        async with self.search_lock:
            if self.current_search_task and not self.current_search_task.done():
                self.current_search_task.cancel()
                try:
                    await asyncio.wait_for(self.current_search_task, timeout=1.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    logger.info("Поиск отменен")
                except Exception as e:
                    logger.error(f"Ошибка при отмене поиска: {e}")
                finally:
                    self.current_search_task = None

    async def cancel_search(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Обработчик отмены поиска из состояния поиска"""
        context.user_data['search_cancelled'] = True
        await update.message.reply_text(
            "❌ Поиск отменен.",
            reply_markup=self._get_main_keyboard()
        )
        context.user_data['in_search'] = False
        return ConversationHandler.END

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        # Инициализируем данные пользователя
        user, created = await self.user_service.get_or_create_telegram_user(update)
        if user:
            context.user_data['db_user'] = user  # Сохраняем объект пользователя в context
            await self.user_service.update_user_activity(user.user_id)
        context.user_data.setdefault('search_history', [])
        context.user_data.setdefault('preferred_platform', 'WB')
        
        welcome_text = (
            "🎉 <b>Добро пожаловать в MultiPlatform Parser Bot!</b>\n\n"
            "Я помогу вам найти лучшие товары на разных маркетплейсах.\n\n"
            f"📦 <b>Текущая платформа:</b> {self._get_platform_display_name()}\n\n"
            "✨ <b>Как пользоваться:</b>\n"
            "• Используйте <b>кнопки меню</b> ниже\n"
            "• Или введите <b>название товара</b> для быстрого поиска\n"
            "• Команда <code>/help</code> - список всех команд\n\n"
            "💡 <b>Начните с кнопок меню или введите товар для поиска!</b>"
        )
        
        await update.message.reply_text(
            welcome_text, 
            parse_mode="HTML",
            reply_markup=self._get_main_keyboard()
        )

    def _get_platform_display_name(self) -> str:
        """Возвращает отображаемое имя текущей платформы"""
        if isinstance(self.current_parser, WildberriesParser):
            return "Wildberries 🛍️"
        elif isinstance(self.current_parser, OzonParser):
            return "Ozon 🟠"
        return "Неизвестно"

    async def switch_platform(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Обработчик смены платформы"""
        await update.message.reply_text(
            "🛒 <b>Выберите платформу для поиска:</b>\n\n"
            "• Wildberries 🛍️ - российский маркетплейс\n"
            "• Ozon 🟠 - одна из крупнейших площадок\n\n"
            "💡 Вы можете менять платформу в любое время!",
            parse_mode="HTML",
            reply_markup=self._get_platform_keyboard()
        )

    async def handle_platform_selection(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Обработка выбора платформы"""
        text = update.message.text.strip()
        
        if text == "Wildberries 🛍️":
            self.current_parser = self.parsers['WB']
            context.user_data['preferred_platform'] = 'WB'
            platform_name = "Wildberries 🛍️"
        elif text == "Ozon 🟠":
            self.current_parser = self.parsers['OZ']
            context.user_data['preferred_platform'] = 'OZ'
            platform_name = "Ozon 🟠"
        elif text == "↩️ Назад в меню":
            await update.message.reply_text(
                "Возвращаемся в главное меню...",
                reply_markup=self._get_main_keyboard()
            )
            return
        else:
            await update.message.reply_text(
                "❌ Неизвестная платформа",
                reply_markup=self._get_platform_keyboard()
            )
            return
        
        await update.message.reply_text(
            f"✅ <b>Платформа изменена на:</b> {platform_name}\n\n"
            "Теперь все поиски будут выполняться на выбранной платформе.",
            parse_mode="HTML",
            reply_markup=self._get_main_keyboard()
        )

    async def help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Расширенная справка с списком команд"""
        help_text = (
            "📚 <b>КОМАНДЫ БОТА</b>\n\n"
            
            "🎯 <b>Основные команды:</b>\n"
            "• /start - начать работу с ботом\n"
            "• /search - расширенный поиск товаров\n"
            "• /history - история ваших запросов\n"
            "• /top - топ товаров по категориям\n"
            "• /platform - сменить платформу\n"
            "• /help - показать это сообщение\n\n"
            
            f"📦 <b>Текущая платформа:</b> {self._get_platform_display_name()}\n\n"
            
            "💎 <b>Доступные кнопки:</b>\n"
            "• 🔍 Поиск товаров - расширенный поиск\n"
            "• 🛒 Сменить платформу - выбор маркетплейса\n"
            "• 🔄 История поиска - ваши прошлые запросы\n"
            "• 🎯 Топ товаров - популярные категории\n"
            "• 💎 Акции - товары со скидками\n\n"
            
            "💡 <b>Совет:</b> Всегда используйте кнопки меню для быстрого доступа к функциям!"
        )
        
        await update.message.reply_text(
            help_text, 
            parse_mode="HTML",
            reply_markup=self._get_main_keyboard()
        )
    
    async def handle_media(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Обработчик для медиафайлов"""
        media_type = "файл"
        if update.message.photo:
            media_type = "фото"
        elif update.message.video:
            media_type = "видео"
        elif update.message.document:
            media_type = "документ"
        elif update.message.location:
            media_type = "геопозиция"
        elif update.message.poll:
            media_type = "опрос"
        
        await update.message.reply_text(
            f"❌ <b>{media_type.capitalize()} не поддерживается</b>\n\n"
            "Этот бот работает только с текстовыми командами.\n\n"
            "💡 <b>Используйте:</b>\n"
            "• Кнопки меню для навигации\n"
            "• Текстовые команды (/help для списка)\n"
            "• Поиск по названию товара",
            parse_mode="HTML",
            reply_markup=self._get_main_keyboard()
        )

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Обработчик текстовых сообщений с кнопками"""
        text = update.message.text.strip()
        
        # Обработка кнопки отмены поиска
        if text == "❌ Отменить поиск":
            # Устанавливаем флаг отмены
            context.user_data['search_cancelled'] = True
            await update.message.reply_text(
                "❌ Поиск отменен.",
                reply_markup=self._get_main_keyboard()
            )
            context.user_data['in_search'] = False
            return
        
        # Обработка кнопки отмены быстрого поиска
        if text == "❌ Отменить поиск" and context.user_data.get('in_quick_search', False):
            context.user_data['quick_search_cancelled'] = True
            await update.message.reply_text(
                "❌ Быстрый поиск отменен.",
                reply_markup=self._get_main_keyboard()
            )
            context.user_data['in_quick_search'] = False
            return
        
        if text == "❌ Отменить поиск":
            await self.cancel_current_search()
            await update.message.reply_text(
                "❌ Поиск отменен.",
                reply_markup=self._get_main_keyboard()
            )
            return
    
        # Обработка выбора платформы
        if text in ["Wildberries 🛍️", "Ozon 🟠"]:
            await self.handle_platform_selection(update, context)
            return
        
        # Обработка остальных кнопок
        if text == "🔍 Поиск товаров":
            await self.search_command(update, context)
        elif text == "🛒 Сменить платформу":
            await self.switch_platform(update, context)
        elif text == "📊 Статистика":
            await self.stats_command(update, context)
        elif text == "🔄 История поиска":
            await self.history_command(update, context)
        elif text == "ℹ️ Помощь":
            await self.help(update, context)
        elif text == "🎯 Топ товаров":
            await self.top_products(update, context)
        elif text == "💎 Акции":
            await self.discount_products(update, context)
        elif text == "↩️ Назад в меню":
            await update.message.reply_text(
                "Возвращаемся в главное меню...",
                reply_markup=self._get_main_keyboard()
            )
        elif text == "🧹 Очистить историю":
            await self.clear_history(update, context)
        elif text == "🔄 Вернуться к истории":
            await self.history_command(update, context)
        elif text == "✅ Да, очистить историю":
            await self.handle_confirmation(update, context)
        elif text == "❌ Нет, отменить":
            await self.handle_confirmation(update, context)
        elif context.user_data.get('awaiting_confirmation', False):
            await self.handle_confirmation(update, context)
        elif text.startswith("🔍 ") and len(text) > 2:
            query = text[2:].strip()
            await self.show_history_products(update, context, query)
        else:
            if context.user_data.get('in_search', False):
                current_state = context.user_data.get('search_state')
                if current_state == SEARCH_QUERY:
                    await self.receive_query(update, context)
                elif current_state == SEARCH_LIMIT:
                    await self.receive_limit(update, context)
            else:
                await self.quick_search(update, context)
    
    async def clear_history(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Очистка истории поиска с подтверждением"""
        keyboard = [
            [KeyboardButton("✅ Да, очистить историю")],
            [KeyboardButton("❌ Нет, отменить")]
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await update.message.reply_text(
            "🗑️ <b>Очистка истории поиска</b>\n\n"
            "Вы уверены что хотите очистить всю историю поиска?\n"
            "Это действие нельзя отменить.",
            parse_mode="HTML",
            reply_markup=reply_markup
        )
        
        # Устанавливаем состояние ожидания подтверждения
        context.user_data['awaiting_confirmation'] = True

    async def handle_confirmation(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Обработка подтверждения очистки истории"""
        text = update.message.text.strip()  # Вот здесь text определяется!
        
        if text == "✅ Да, очистить историю":
            user = context.user_data.get('db_user')
            if user:
                try:
                    from asgiref.sync import sync_to_async
                    from app.models import UserSearchHistory
                    # УДАЛЯЕМ ИСТОРИЮ ПОЛЬЗОВАТЕЛЯ ИЗ БАЗЫ ДАННЫХ
                    await sync_to_async(
                        lambda: UserSearchHistory.objects.filter(user=user).delete()
                    )()
                    logger.info(f"История поиска очищена для user_id={user.user_id}")
                except Exception as e:
                    logger.error(f"Ошибка очистки истории из БД: {e}")
            
            # Также очищаем локальный кеш
            context.user_data['search_history'] = []
            context.user_data['awaiting_confirmation'] = False
            
            await update.message.reply_text(
                "✅ <b>История поиска успешно очищена!</b>",
                parse_mode="HTML",
                reply_markup=self._get_main_keyboard()
            )
        
        elif text == "❌ Нет, отменить":
            context.user_data['awaiting_confirmation'] = False
            await update.message.reply_text(
                "❌ <b>Очистка истории отменена</b>",
                parse_mode="HTML",
                reply_markup=self._get_main_keyboard()
            )
    
    async def show_history_products(self, update: Update, context: ContextTypes.DEFAULT_TYPE, query: str = None):
        """Показ товаров из истории поиска"""
        if query is None:
            text = update.message.text.strip()
            if text.startswith("🔍 "):
                query = text[2:].strip()
        
        # Проверяем что query не является командой
        if query in ["🔄 Вернуться к истории", "✅ Да, очистить историю", "❌ Нет, отменить"]:
            await self.history_command(update, context)
            return
        
        # Ищем запрос в истории
        search_history = context.user_data.get('search_history', [])
        found_history = None
        
        for history_item in search_history:
            if history_item.get('query', '') == query:
                found_history = history_item
                break
        
        if not found_history or 'products' not in found_history:
            await update.message.reply_text(
                f"❌ <b>Не удалось найти товары для запроса:</b> <code>{query}</code>",
                parse_mode="HTML",
                reply_markup=self._get_history_keyboard(search_history)
            )
            return
        
        products = found_history['products']
        timestamp = found_history.get('timestamp', '')
        
        # Красивое сообщение перед показом товаров
        await update.message.reply_text(
            f"📦 <b>РЕЗУЛЬТАТЫ ПОИСКА</b>\n\n"
            f"🔍 <b>Запрос:</b> <code>{query}</code>\n"
            f"📊 <b>Найдено товаров:</b> <b>{len(products)}</b>\n"
            f"🕒 <b>Время поиска:</b> {timestamp}\n\n"
            f"<i>Отправляю найденные товары...</i>",
            parse_mode="HTML"
        )
        
        # Отправляем товары
        await self.send_all_products(update, products)
        
        # Кнопка возврата к истории
        keyboard = [[KeyboardButton("🔄 Вернуться к истории")], [KeyboardButton("↩️ Назад в меню")]]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await update.message.reply_text(
            f"✅ <b>Показано {len(products)} товаров по запросу:</b> <code>{query}</code>",
            parse_mode="HTML",
            reply_markup=reply_markup
        )
    
    async def _clean_old_history(self, context: ContextTypes.DEFAULT_TYPE):
        """Очистка истории старше 24 часов"""
        if 'search_history' not in context.user_data:
            return
        
        from datetime import datetime, timedelta
        history = context.user_data['search_history']
        twenty_four_hours_ago = datetime.now() - timedelta(hours=24)
        
        # Фильтруем историю
        filtered_history = []
        for item in history:
            timestamp_str = item.get('timestamp', '')
            try:
                item_time = datetime.strptime(timestamp_str, "%d.%m.%Y %H:%M")
                if item_time >= twenty_four_hours_ago:
                    filtered_history.append(item)
            except:
                # Если не удалось распарсить время, оставляем запись
                filtered_history.append(item)
        
        context.user_data['search_history'] = filtered_history

    async def top_products(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Топ товаров для бесплатного бота"""
        categories = {
            "🔥 Электроника": "смартфон",
            "👟 Одежда и обувь": "кроссовки", 
            "💄 Красота": "духи",
            "🏠 Дом": "диван",
            "🎮 Развлечения": "игра",
            "🎲 Случайная категория": "популярные товары"
        }
        
        keyboard = []
        for category_name in categories.keys():
            keyboard.append([KeyboardButton(f"{category_name}")])
        
        keyboard.append([KeyboardButton("↩️ Назад в меню")])
        
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await update.message.reply_text(
            "🎯 <b>Топ популярных категорий:</b>\n\n"
            "Выберите категорию для просмотра интересных товаров:",
            parse_mode="HTML",
            reply_markup=reply_markup
        )

    async def show_top_products(self, update: Update, context: ContextTypes.DEFAULT_TYPE, category: str):
        """Показ топовых товаров для выбранной категории"""
        query = self._get_query_for_category(category)
        
        # Используем стратегию для бесплатного бота
        products = await asyncio.to_thread(
            self.current_parser.search_products_with_strategy,
            query,
            limit=5,
            strategy="popular_midrange"
        )
        
        if not products:
            await update.message.reply_text(
                f"❌ Не удалось найти товары в категории '{category}'\n"
                "Попробуйте другую категорию или повторите позже",
                reply_markup=self._get_main_keyboard()
            )
            return
        
        await update.message.reply_text(
            f"🎯 <b>Топ товаров в категории:</b> {category}\n\n"
            f"🔍 Запрос: <code>{query}</code>\n"
            f"📦 Найдено: {len(products)} товаров",
            parse_mode="HTML"
        )
        
        await self.send_all_products(update, products)

    def _get_query_for_category(self, category: str) -> str:
        """Возвращает поисковый запрос для категории"""
        category_mapping = {
            "🔥 Электроника": "смартфон",
            "👟 Одежда и обувь": "кроссовки",
            "💄 Красота": "духи",
            "🏠 Дом": "диван",
            "🎮 Развлечения": "игра",
            "💰 Суперскидки": "скидка 70",
            "⭐ Высокий рейтинг": "рейтинг 5",
            "🚀 Быстрая доставка": "доставка завтра",
            "🎯 Топ по отзывам": "отзывов 1000",
            "🎲 Случайная категория": self._get_random_category_query()
        }
        
        return category_mapping.get(category, "популярные товары")

    def _get_random_category_query(self) -> str:
        """Случайный запрос из популярных категорий"""
        import random
        random_queries = [
            "смартфон", "кроссовки", "духи", "диван", "игра",
            "платье", "часы", "ноутбук", "телевизор", "кофе",
            "чай", "игрушка", "книга", "сумка", "кофта"
        ]
        return random.choice(random_queries)

    async def discount_products(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Товары со скидками"""
        discount_categories = ["👟 Со скидкой", "👕 Распродажа", "📱 Уценка", "💄 Акция"]
        
        keyboard = []
        for category in discount_categories:
            keyboard.append([KeyboardButton(f"💎 {category}")])
        keyboard.append([KeyboardButton("↩️ Назад в меню")])
        
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await update.message.reply_text(
            "💎 <b>Товары со скидками:</b>\n\n"
            "Выберите категорию для просмотра акционных товаров:",
            parse_mode="HTML",
            reply_markup=reply_markup
        )

    async def search_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Начало процесса поиска"""
        platform_name = self._get_platform_display_name()
        
        await update.message.reply_text(
            f"🔍 <b>Расширенный поиск на {platform_name}</b>\n\n"
            "Введите название товара для поиска:",
            parse_mode="HTML",
            reply_markup=ReplyKeyboardMarkup([[KeyboardButton("↩️ Назад в меню")]], 
                                        resize_keyboard=True)
        )
        
        context.user_data['in_search'] = True
        context.user_data['search_state'] = SEARCH_QUERY
        return SEARCH_QUERY
    
    def _get_history_keyboard(self, search_history: list):
        """Клавиатура для истории поиска"""
        keyboard = []
        
        # Добавляем кнопки для последних запросов
        for history_item in search_history[:5]:
            query = history_item.get('query', '')
            display_query = query[:18] + "..." if len(query) > 21 else query
            keyboard.append([KeyboardButton(f"🔍 {display_query}")])
        
        keyboard.append([KeyboardButton("🧹 Очистить историю")])
        keyboard.append([KeyboardButton("↩️ Назад в меню")])
        
        return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

    async def receive_query(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Обработка поискового запроса"""
        text = update.message.text.strip()
        
        if text == "↩️ Назад в меню":
            await update.message.reply_text(
                "Возвращаемся в главное меню...",
                reply_markup=self._get_main_keyboard()
            )
            context.user_data['in_search'] = False
            return ConversationHandler.END
        
        query = text.strip()
        if len(query) < 2:
            await update.message.reply_text("❌ Слишком короткий запрос. Попробуйте еще раз.")
            return SEARCH_QUERY
        
        context.user_data['query'] = query
        context.user_data['search_cancelled'] = False  # Сбрасываем флаг отмены

        
        # Предлагаем выбрать количество товаров
        keyboard = [
            [KeyboardButton("5 товаров"), KeyboardButton("10 товаров")],
            [KeyboardButton("15 товаров"), KeyboardButton("20 товаров")],
            [KeyboardButton("↩️ Назад в меню")]
        ]
        reply_markup = self._get_search_keyboard()     

        platform_name = self._get_platform_display_name()
        await update.message.reply_text(
            f"🔍 Вы ищете на {platform_name}: <b>{query}</b>\n\nТеперь выберите количество товаров:",
            parse_mode="HTML",
            reply_markup=reply_markup
        )
        
        context.user_data['search_state'] = SEARCH_LIMIT
        return SEARCH_LIMIT

    async def receive_limit(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Обработка выбора количества товаров с мгновенной отменой"""
        text = update.message.text.strip()

        # Обработка кнопки возврата в меню
        if text == "↩️ Назад в меню":
            await update.message.reply_text(
                "Возвращаемся в главное меню...",
                reply_markup=self._get_main_keyboard()
            )
            context.user_data['in_search'] = False
            return ConversationHandler.END

        # Обработка кнопки отмены поиска
        if text == "❌ Отменить поиск":
            await self.cancel_current_search()
            await update.message.reply_text(
                "❌ Поиск отменен.",
                reply_markup=self._get_main_keyboard()
            )
            context.user_data['in_search'] = False
            return ConversationHandler.END

        # Проверяем, что выбран допустимый лимит
        if text not in ["5 товаров", "10 товаров", "15 товаров", "20 товаров"]:
            await update.message.reply_text(
                "❌ Пожалуйста, выберите количество товаров из предложенных вариантов.",
                reply_markup=self._get_search_keyboard()
            )
            return SEARCH_LIMIT
        
        # Определяем лимит
        limit_mapping = {
            "5 товаров": 5,
            "10 товаров": 10, 
            "15 товаров": 15,
            "20 товаров": 20
        }
        limit = limit_mapping[text]
        
        query = context.user_data.get('query', '')
        platform_name = self._get_platform_display_name()
        
        # Показываем кнопку отмены поиска
        cancel_keyboard = self._get_cancel_keyboard()
        
        search_msg = await update.message.reply_text(
            f"🔍 <b>Ищу {limit} товаров на {platform_name} по запросу:</b> <code>{query}</code>\n\n"
            "⏳ Это может занять несколько секунд...\n"
            "❌ Вы можете отменить поиск в любой момент",
            parse_mode="HTML",
            reply_markup=cancel_keyboard
        )
        
        # Сохраняем ID сообщения для возможного редактирования
        context.user_data['search_message_id'] = search_msg.message_id
        
        try:
            # Отменяем предыдущий поиск если он есть
            await self.cancel_current_search()
            
            # Создаем задачу поиска
            async with self.search_lock:
                self.current_search_task = asyncio.create_task(
                    self._execute_extended_search(update, context, query, limit, search_msg)
                )
            
            # Ждем завершения задачи поиска
            await self.current_search_task
            
        except asyncio.CancelledError:
            # Поиск был отменен пользователем
            try:
                await search_msg.edit_text(
                    "❌ Поиск отменен.",
                    reply_markup=self._get_main_keyboard()
                )
            except Exception:
                # Если не удалось отредактировать, отправляем новое сообщение
                await update.message.reply_text(
                    "❌ Поиск отменен.",
                    reply_markup=self._get_main_keyboard()
                )
        except asyncio.TimeoutError:
            try:
                await search_msg.edit_text(
                    "⏰ <b>Превышено время ожидания поиска</b>\n\n"
                    "🔧 Попробуйте повторить запрос позже",
                    parse_mode="HTML",
                    reply_markup=self._get_main_keyboard()
                )
            except Exception:
                await update.message.reply_text(
                    "⏰ <b>Превышено время ожидания поиска</b>",
                    parse_mode="HTML",
                    reply_markup=self._get_main_keyboard()
                )
        except Exception as e:
            logger.error("Ошибка расширенного поиска: %s", str(e), exc_info=True)
            try:
                await search_msg.edit_text(
                    f"⚠️ <b>Произошла ошибка при поиске:</b>\n{str(e)[:100]}...",
                    parse_mode="HTML",
                    reply_markup=self._get_main_keyboard()
                )
            except Exception:
                await update.message.reply_text(
                    f"⚠️ <b>Произошла ошибка при поиске:</b>\n{str(e)[:100]}...",
                    parse_mode="HTML",
                    reply_markup=self._get_main_keyboard()
                )
        finally:
            # Всегда сбрасываем флаги поиска
            context.user_data['in_search'] = False
            async with self.search_lock:
                self.current_search_task = None
        
        return ConversationHandler.END

    async def _execute_extended_search(self, update: Update, context: ContextTypes.DEFAULT_TYPE, 
                                query: str, limit: int, search_msg):
        """Выполнение расширенного поиска с проверкой отмены"""
        platform_name = self._get_platform_display_name()
        
        try:
            # Анимация поиска с проверкой отмены
            dots = ["", ".", "..", "..."]
            for i in range(4):
                if self.current_search_task and self.current_search_task.cancelled():
                    raise asyncio.CancelledError()
                    
                try:
                    # Используем try/except для обработки ошибок редактирования
                    await search_msg.edit_text(
                        f"🔍 <b>Ищу {limit} товаров на {platform_name} по запросу:</b> <code>{query}</code>{dots[i % 4]}\n\n"
                        "⏳ Это может занять несколько секунд...\n"
                        "❌ Вы можете отменить поиск в любой момент",
                        parse_mode="HTML",
                        reply_markup=self._get_cancel_keyboard()
                    )
                except Exception as e:
                    logger.debug(f"Не удалось обновить сообщение поиска: {e}")
                    # Продолжаем выполнение, даже если не удалось обновить сообщение
                
                await asyncio.sleep(0.4)
            
            # Проверяем отмену перед началом тяжелой работы
            if self.current_search_task and self.current_search_task.cancelled():
                raise asyncio.CancelledError()
            
            # Выполняем поиск в отдельном потоке
            loop = asyncio.get_event_loop()
            raw_products = await asyncio.wait_for(
                loop.run_in_executor(self.executor, self.current_parser.search_products, query, limit),
                timeout=25.0
            )
            
            # Проверяем отмену после поиска
            if self.current_search_task and self.current_search_task.cancelled():
                raise asyncio.CancelledError()
            
            if not raw_products:
                try:
                    await search_msg.edit_text(
                        f"❌ <b>По запросу</b> <code>{query}</code> <b>ничего не найдено на {platform_name}</b>\n\n"
                        "💡 Попробуйте изменить запрос или сменить платформу",
                        parse_mode="HTML",
                        reply_markup=self._get_main_keyboard()
                    )
                except Exception:
                    # Если не удалось отредактировать, отправляем новое сообщение
                    await update.message.reply_text(
                        f"❌ <b>По запросу</b> <code>{query}</code> <b>ничего не найдено на {platform_name}</b>",
                        parse_mode="HTML",
                        reply_markup=self._get_main_keyboard()
                    )
                return
            
            # Сохраняем товары
            saved_count = await self.current_parser.parse_and_save_async(query, limit)
            
            # Проверяем отмену после сохранения
            if self.current_search_task and self.current_search_task.cancelled():
                raise asyncio.CancelledError()
            
            if saved_count == 0:
                try:
                    await search_msg.edit_text(
                        f"❌ <b>Не удалось сохранить товары с {platform_name}</b>\n\n"
                        "⚠️ Попробуйте другой запрос",
                        parse_mode="HTML",
                        reply_markup=self._get_main_keyboard()
                    )
                except Exception:
                    await update.message.reply_text(
                        f"❌ <b>Не удалось сохранить товары с {platform_name}</b>",
                        parse_mode="HTML",
                        reply_markup=self._get_main_keyboard()
                    )
                return
            
            # Получаем товары из базы
            from app.models import Product
            
            product_ids = [str(p.get('product_id')) for p in raw_products if p.get('product_id')]
            
            if product_ids:
                products = await loop.run_in_executor(
                    self.executor,
                    lambda: list(Product.objects.filter(
                        product_id__in=product_ids, 
                        platform=self.current_parser.platform
                    ))
                )
            else:
                products = await loop.run_in_executor(
                    self.executor,
                    lambda: list(Product.objects.filter(
                        platform=self.current_parser.platform
                    ).order_by('-id')[:limit])
                )
            
            # Проверяем отмену перед отправкой результатов
            if self.current_search_task and self.current_search_task.cancelled():
                raise asyncio.CancelledError()
            
            if not products:
                try:
                    await search_msg.edit_text(
                        f"❌ <b>Не удалось загрузить товары с {platform_name}</b>\n\n"
                        "⚠️ Попробуйте еще раз",
                        parse_mode="HTML",
                        reply_markup=self._get_main_keyboard()
                    )
                except Exception:
                    await update.message.reply_text(
                        f"❌ <b>Не удалось загрузить товары с {platform_name}</b>",
                        parse_mode="HTML",
                        reply_markup=self._get_main_keyboard()
                    )
                return
            
            # Сохраняем результаты в context
            context.user_data['last_results'] = products
            context.user_data['query'] = query
            
            try:
                await search_msg.edit_text(
                    f"✅ <b>Найдено и сохранено {saved_count} товаров с {platform_name}</b>\n\n"
                    "📦 Отправляю результаты...",
                    parse_mode="HTML"
                )
            except Exception:
                # Если не удалось отредактировать, просто продолжаем
                pass
            
            # Преобразуем в формат для отправки
            products_for_sending = []
            for product in products:
                products_for_sending.append({
                    'product_id': str(product.product_id),
                    'name': product.name,
                    'price': float(product.price),
                    'discount_price': float(product.discount_price) if product.discount_price else None,
                    'rating': float(product.rating) if product.rating else 0.0,
                    'reviews_count': product.reviews_count,
                    'product_url': product.product_url,
                    'image_url': product.image_url,
                    'quantity': product.quantity,
                    'is_available': product.is_available,
                    'platform': product.platform
                })
            
            # Сохраняем историю поиска
            platform_name_display = self._get_platform_display_name()
            await self._save_search_history(update, context, query, products_for_sending, platform_name_display)
            
            # Проверяем отмену перед отправкой товаров
            if self.current_search_task and self.current_search_task.cancelled():
                raise asyncio.CancelledError()
            
            # Отправляем товары
            await self.send_all_products(update, products_for_sending)
            
            # Финальное сообщение
            await update.message.reply_text(
                f"🎉 <b>Поиск завершен! Найдено {saved_count} товаров</b>\n\n"
                "💡 Используйте кнопки для новых запросов:",
                parse_mode="HTML",
                reply_markup=self._get_main_keyboard()
            )
            
        except asyncio.CancelledError:
            raise  # Пробрасываем отмену выше
        except Exception as e:
            logger.error("Ошибка выполнения расширенного поиска: %s", str(e), exc_info=True)
            # Отправляем сообщение об ошибке, если поиск не был отменен
            if not (self.current_search_task and self.current_search_task.cancelled()):
                try:
                    await search_msg.edit_text(
                        f"⚠️ <b>Произошла ошибка при поиске:</b>\n{str(e)[:100]}...",
                        parse_mode="HTML",
                        reply_markup=self._get_main_keyboard()
                    )
                except Exception:
                    await update.message.reply_text(
                        f"⚠️ <b>Произошла ошибка при поиске:</b>\n{str(e)[:100]}...",
                        parse_mode="HTML",
                        reply_markup=self._get_main_keyboard()
                    )
            raise

    async def quick_search(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Быстрый поиск с мгновенной отменой"""
        query = update.message.text.strip()
        
        if query == "❌ Отменить поиск":
            await self.cancel_current_search()
            await update.message.reply_text(
                "❌ Поиск отменен.",
                reply_markup=self._get_main_keyboard()
            )
            return
        
        if len(query) < 2:
            await update.message.reply_text("❌ Слишком короткий запрос. Попробуйте еще раз.")
            return
        
        # Отменяем предыдущий поиск если он есть
        await self.cancel_current_search()
        
        # Показываем кнопку отмены
        cancel_keyboard = self._get_quick_search_keyboard()
        
        platform_name = self._get_platform_display_name()
        
        search_msg = await update.message.reply_text(
            f"🔍 <b>Ищу товары на {platform_name} по запросу:</b> <code>{query}</code>\n\n"
            "⏳ Это может занять несколько секунд...\n"
            "❌ Вы можете отменить поиск в любой момент",
            parse_mode="HTML",
            reply_markup=cancel_keyboard
        )
        
        # Создаем задачу поиска и передаем context
        async with self.search_lock:
            self.current_search_task = asyncio.create_task(
                self._execute_quick_search(update, context, query, search_msg)
            )
        
        try:
            await self.current_search_task
        except asyncio.CancelledError:
            # Поиск был отменен - это нормально
            try:
                await search_msg.edit_text(
                    "❌ Поиск отменен.",
                    reply_markup=self._get_main_keyboard()
                )
            except Exception:
                await update.message.reply_text(
                    "❌ Поиск отменен.",
                    reply_markup=self._get_main_keyboard()
                )
        except Exception as e:
            logger.error("Ошибка быстрого поиска: %s", str(e))
            try:
                await search_msg.edit_text(
                    f"⚠️ <b>Ошибка поиска:</b> {str(e)[:100]}...",
                    parse_mode="HTML",
                    reply_markup=self._get_main_keyboard()
                )
            except Exception:
                await update.message.reply_text(
                    f"⚠️ <b>Ошибка поиска:</b> {str(e)[:100]}...",
                    parse_mode="HTML",
                    reply_markup=self._get_main_keyboard()
                )

    async def _execute_quick_search(self, update: Update, context: ContextTypes.DEFAULT_TYPE, query: str, search_msg):
        """Выполнение быстрого поиска с проверкой отмены"""
        try:
            platform_name = self._get_platform_display_name()
            
            # Анимация поиска с проверкой отмены
            dots = ["", ".", "..", "..."]
            for i in range(6):
                if self.current_search_task and self.current_search_task.cancelled():
                    raise asyncio.CancelledError()
                    
                try:
                    await search_msg.edit_text(
                        f"🔍 <b>Ищу товары на {platform_name} по запросу:</b> <code>{query}</code>{dots[i % 4]}\n\n"
                        "⏳ Это может занять несколько секунд...\n"
                        "❌ Вы можете отменить поиск в любой момент",
                        parse_mode="HTML",
                        reply_markup=self._get_quick_search_keyboard()
                    )
                except Exception:
                    # Пропускаем ошибки редактирования, продолжаем поиск
                    pass
                
                await asyncio.sleep(0.3)
            
            if self.current_search_task and self.current_search_task.cancelled():
                raise asyncio.CancelledError()
            
            # Выполняем поиск в отдельном потоке
            loop = asyncio.get_event_loop()
            raw_products = await asyncio.wait_for(
                loop.run_in_executor(self.executor, self.current_parser.search_products, query, 10),
                timeout=30.0
            )
            
            if self.current_search_task and self.current_search_task.cancelled():
                raise asyncio.CancelledError()
            
            if not raw_products:
                try:
                    await search_msg.edit_text(
                        f"❌ <b>По запросу</b> <code>{query}</code> <b>ничего не найдено на {platform_name}</b>\n\n"
                        "💡 Попробуйте изменить запрос или сменить платформу",
                        parse_mode="HTML",
                        reply_markup=self._get_main_keyboard()
                    )
                except Exception:
                    await update.message.reply_text(
                        f"❌ <b>По запросу</b> <code>{query}</code> <b>ничего не найдено на {platform_name}</b>",
                        parse_mode="HTML",
                        reply_markup=self._get_main_keyboard()
                    )
                return
            
            # Сохраняем товары
            saved_count = await self.current_parser.parse_and_save_async(query, 10)
            
            if self.current_search_task and self.current_search_task.cancelled():
                raise asyncio.CancelledError()
            
            if saved_count == 0:
                try:
                    await search_msg.edit_text(
                        f"❌ <b>Не удалось сохранить товары с {platform_name}</b>\n\n"
                        "⚠️ Попробуйте другой запрос",
                        parse_mode="HTML",
                        reply_markup=self._get_main_keyboard()
                    )
                except Exception:
                    await update.message.reply_text(
                        f"❌ <b>Не удалось сохранить товары с {platform_name}</b>",
                        parse_mode="HTML",
                        reply_markup=self._get_main_keyboard()
                    )
                return
            
            # Получаем товары из базы
            from app.models import Product
            
            product_ids = [str(p.get('product_id')) for p in raw_products if p.get('product_id')]
            
            if product_ids:
                products = await loop.run_in_executor(
                    self.executor,
                    lambda: list(Product.objects.filter(
                        product_id__in=product_ids, 
                        platform=self.current_parser.platform
                    ))
                )
            else:
                products = await loop.run_in_executor(
                    self.executor,
                    lambda: list(Product.objects.filter(
                        platform=self.current_parser.platform
                    ).order_by('-id')[:10])
                )
            
            if self.current_search_task and self.current_search_task.cancelled():
                raise asyncio.CancelledError()
            
            if not products:
                try:
                    await search_msg.edit_text(
                        f"❌ <b>Не удалось загрузить товары с {platform_name}</b>\n\n"
                        "⚠️ Попробуйте еще раз",
                        parse_mode="HTML",
                        reply_markup=self._get_main_keyboard()
                    )
                except Exception:
                    await update.message.reply_text(
                        f"❌ <b>Не удалось загрузить товары с {platform_name}</b>",
                        parse_mode="HTML",
                        reply_markup=self._get_main_keyboard()
                    )
                return
            
            # Сохраняем в context
            context.user_data['last_results'] = products
            context.user_data['query'] = query
            
            try:
                await search_msg.edit_text(
                    f"✅ <b>Найдено и сохранено {saved_count} товаров с {platform_name}</b>\n\n"
                    "📦 Отправляю результаты...",
                    parse_mode="HTML"
                )
            except Exception:
                # Если не удалось отредактировать, просто продолжаем
                pass
            
            # Преобразуем в формат для отправки
            products_for_sending = []
            for product in products:
                products_for_sending.append({
                    'product_id': str(product.product_id),
                    'name': product.name,
                    'price': float(product.price),
                    'discount_price': float(product.discount_price) if product.discount_price else None,
                    'rating': float(product.rating) if product.rating else 0.0,
                    'reviews_count': product.reviews_count,
                    'product_url': product.product_url,
                    'image_url': product.image_url,
                    'quantity': product.quantity,
                    'is_available': product.is_available,
                    'platform': product.platform
                })
            
            # Сохраняем историю поиска
            platform_name_display = self._get_platform_display_name()
            await self._save_search_history(update, context, query, products_for_sending, platform_name_display)
            
            if self.current_search_task and self.current_search_task.cancelled():
                raise asyncio.CancelledError()
            
            # Отправляем товары
            await self.send_all_products(update, products_for_sending)
            
            await update.message.reply_text(
                "🎉 <b>Поиск завершен!</b>\n\n"
                "💡 Используйте кнопки для новых запросов:",
                parse_mode="HTML",
                reply_markup=self._get_main_keyboard()
            )
            
        except asyncio.TimeoutError:
            try:
                await search_msg.edit_text(
                    "⏰ <b>Превышено время ожидания поиска</b>\n\n"
                    "🔧 Попробуйте повторить запрос позже",
                    parse_mode="HTML",
                    reply_markup=self._get_main_keyboard()
                )
            except Exception:
                await update.message.reply_text(
                    "⏰ <b>Превышено время ожидания поиска</b>",
                    parse_mode="HTML",
                    reply_markup=self._get_main_keyboard()
                )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("Ошибка выполнения поиска: %s", str(e))
            # Отправляем сообщение об ошибке, если поиск не был отменен
            if not (self.current_search_task and self.current_search_task.cancelled()):
                try:
                    await search_msg.edit_text(
                        f"⚠️ <b>Произошла ошибка при поиске:</b>\n{str(e)[:100]}...",
                        parse_mode="HTML",
                        reply_markup=self._get_main_keyboard()
                    )
                except Exception:
                    await update.message.reply_text(
                        f"⚠️ <b>Произошла ошибка при поиске:</b>\n{str(e)[:100]}...",
                        parse_mode="HTML",
                        reply_markup=self._get_main_keyboard()
                    )
            raise
    
    async def send_all_products(self, update: Update, products: List[dict]) -> None:
        """Отправка всех товаров по одному в сообщении"""
        if not products:
            logger.warning("Нет товаров для отправки!")
            await update.message.reply_text("❌ Нет товаров для отправки.")
            return
            
        total_count = len(products)
        logger.info("Начинаем отправку %s товаров", total_count)
        
        # Отправляем заголовок
        await update.message.reply_text(
            f"📦 <b>Найдено {total_count} товаров:</b>",
            parse_mode="HTML"
        )
        
        sent_count = 0
        for index, product in enumerate(products):
            try:
                logger.info("Отправляем товар %s/%s: %s", index+1, total_count, product.get('name'))
                # Отправляем каждый товар отдельным сообщением
                await self.send_product_card(update, product, index, total_count)
                sent_count += 1
                await asyncio.sleep(0.3)  # Небольшая задержка
            except Exception as e:
                logger.error("Ошибка отправки товара %s: %s", index, str(e))
        
        # Итоговое сообщение
        logger.info("Успешно отправлено %s из %s товаров", sent_count, total_count)
        if sent_count > 0:
            await update.message.reply_text(
                f"✅ <b>Отправлено {sent_count} из {total_count} товаров</b>",
                parse_mode="HTML",
                reply_markup=self._get_main_keyboard()
            )
    
    async def check_db(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Команда для проверки базы данных"""
        from app.models import Product
        try:
            count = await asyncio.to_thread(lambda: Product.objects.count())
            products = await asyncio.to_thread(
                lambda: list(Product.objects.all().order_by('-id')[:5])
            )
            
            text = f"📊 <b>База данных:</b>\n\n"
            text += f"• Всего товаров: <b>{count}</b>\n"
            text += f"• Последние 5 товаров:\n"
            
            for i, p in enumerate(products, 1):
                text += f"  {i}. {p.product_id} - {p.name}\n"
            
            await update.message.reply_text(text, parse_mode="HTML")
            
        except Exception as e:
            await update.message.reply_text(f"❌ Ошибка проверки базы: {str(e)}")

    async def _is_image_available(self, image_url: str) -> bool:
        """Проверка доступности изображения"""
        try:
            if self.session is None:
                await self.init_session()
                
            async with self.session.head(image_url, timeout=aiohttp.ClientTimeout(total=5)) as response:
                return response.status == 200
        except Exception as e:
            logger.debug(f"Изображение недоступно {image_url}: {e}")
            return False
    
    def _is_button(self, text: str) -> bool:
        """Проверяет является ли текст кнопкой"""
        buttons = [
            "🔍 Поиск товаров", "📊 Статистика", "🔄 История поиска", "ℹ️ Помощь",
            "🎯 Топ товаров", "💎 Акции", "↩️ Назад в меню", "🧹 Очистить историю",
            "🔄 Вернуться к истории", "✅ Да, очистить историю", "❌ Нет, отменить",
            "5 товаров", "10 товаров", "15 товаров", "20 товаров", "↩️ Назад"
        ]
        return text in buttons
    
    async def send_product_card(self, update: Update, product: dict, 
                          current_index: int, total_count: int) -> None:
        """Отправка одной карточки товара"""
        try:
            caption = self._generate_caption(product, current_index, total_count)
            image_url = product.get('image_url')
            
            if image_url and not self._is_bad_url(image_url):
                # Пробуем отправить с фото
                try:
                    await update.message.reply_photo(
                        photo=image_url,
                        caption=caption,
                        parse_mode="HTML"
                    )
                    return
                except Exception as e:
                    logger.warning("Не удалось отправить фото: %s", str(e))
            
            # Fallback: отправляем только текст
            await update.message.reply_text(caption, parse_mode="HTML")
                
        except Exception as e:
            logger.error("Ошибка отправки карточки: %s", str(e))
    
    def _is_bad_url(self, url: str) -> bool:
        """Проверяет, является ли URL плохим (placeholder или нерабочим)"""
        if not url:
            return True
        bad_patterns = [
            'via.placeholder.com',
            'placeholder',
            'no+image',
            'no_image',
            'example.com',
            'dummyimage.com',
            'broken',
            'error',
        ]
        return any(pattern in url.lower() for pattern in bad_patterns)

    async def send_product_with_image(self, update: Update, product: dict, 
                               current_index: int, total_count: int) -> None:
        """Упрощенная отправка товара с изображением"""
        try:
            image_url = product.get('image_url')
            caption = self._generate_caption(product, current_index, total_count)
            
            if image_url:
                await update.message.reply_photo(
                    photo=image_url,
                    caption=caption,
                    parse_mode="HTML"
                )
            else:
                await update.message.reply_text(caption, parse_mode="HTML")
                
        except Exception as e:
            logger.error("Ошибка отправки товара с изображением: %s", str(e))
            # Fallback: отправляем только текст
            caption = self._generate_caption(product, current_index, total_count)
            await update.message.reply_text(caption, parse_mode="HTML")

    async def _find_alternative_image(self, product_id: str) -> Optional[str]:
        """Поиск альтернативного изображения"""
        try:
            if not product_id:
                return None
                
            # Получаем все возможные URL изображений через парсер
            image_urls = await asyncio.to_thread(
                self.current_parser._generate_all_image_urls, 
                int(product_id)
            )
            
            # Проверяем доступность каждого URL
            for url in image_urls:
                if await self._is_image_available(url):
                    return url
                    
            return None
        except Exception as e:
            logger.error(f"Ошибка поиска альтернативного изображения: {e}")
            return None

    async def _try_direct_url_send(self, update: Update, image_url: str, caption: str) -> bool:
        """Попытка прямой отправки по URL"""
        try:
            await update.message.reply_photo(
                photo=image_url,
                caption=caption,
                parse_mode="HTML"
            )
            logger.info(f"Успешная прямая отправка: {image_url}")
            return True
        except Exception as e:
            logger.debug(f"Прямая отправка не удалась: {e}")
            return False

    async def _try_download_and_send(self, update: Update, image_url: str, caption: str) -> bool:
        """Загрузка и отправка изображения"""
        try:
            # Инициализируем сессию если нужно
            if self.session is None:
                await self.init_session()
                
            # Загружаем изображение
            img_data = await self._download_image(image_url)
            if not img_data:
                return False
                
            img_bytes, content_type = img_data
            
            # Проверяем размер файла
            file_size = len(img_bytes.getvalue())
            if file_size > 10 * 1024 * 1024:  # 10MB limit
                logger.warning(f"Изображение слишком большое: {file_size} bytes")
                return False
                
            # Отправляем изображение
            await update.message.reply_photo(
                photo=img_bytes,
                caption=caption,
                parse_mode="HTML"
            )
            logger.info(f"Успешная отправка загруженного изображения: {image_url}")
            return True
            
        except Exception as e:
            logger.error(f"Ошибка загрузки/отправки: {e}")
            return False

    async def _download_image(self, url: str) -> Optional[Tuple[BytesIO, str]]:
        """Асинхронная загрузка изображения"""
        try:
            if self.session is None:
                await self.init_session()
                
            async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as response:
                if response.status == 200:
                    content_type = response.headers.get('Content-Type', '')
                    if content_type.startswith('image/'):
                        image_data = await response.read()
                        img_bytes = BytesIO(image_data)
                        return img_bytes, content_type
        except Exception as e:
            logger.error(f"Ошибка загрузки изображения {url}: {e}")
        return None

    async def send_product_text_only(self, update: Update, product: dict, 
                                  current_index: int, total_count: int) -> None:
        """Отправка товара только текстом"""
        try:
            text = self._generate_caption(product, current_index, total_count)
            await update.message.reply_text(text, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Ошибка отправки текстовой версии: {e}")

    def _generate_caption(self, product: dict, current_index: int, total_count: int) -> str:
        """Генерация подписи для товара с учетом платформы"""
        name = product.get('name', 'Без названия')
        price = product.get('price', 0)
        discount_price = product.get('discount_price')
        rating = product.get('rating', 0)
        reviews = product.get('reviews_count', 0)
        product_url = product.get('product_url', '')
        product_id = product.get('product_id', 'N/A')
        quantity = product.get('quantity', 0)
        is_available = product.get('is_available', False)
        platform = product.get('platform', 'WB')
        
        # ПРАВИЛЬНОЕ определение платформы
        if platform in ['wildberries', 'WB', 'Wildberries']:
            platform_emoji = "🛍️"
            platform_name = "Wildberries"
            platform_hashtag = "#wildberries"
        else:  # Ozon
            platform_emoji = "🟠" 
            platform_name = "Ozon"
            platform_hashtag = "#ozon"
        
        # Форматируем цены
        price_str = f"<b>{price:,.0f} ₽</b>".replace(',', ' ')
        
        # Создаем текст
        text = f"{platform_emoji} <b>{name}</b>\n\n"
        
        # Блок с артикулом и платформой
        text += f"📋 <b>Артикул:</b> <code>{product_id}</code>\n"
        text += f"🏪 <b>Платформа:</b> {platform_name} {platform_emoji}\n"
        
        # Блок с ценами
        if discount_price and discount_price < price:
            discount_percent = int((1 - discount_price / price) * 100)
            discount_price_str = f"<b>{discount_price:,.0f} ₽</b>".replace(',', ' ')
            original_price_str = f"<s>{price:,.0f} ₽</s>".replace(',', ' ')
            
            text += f"💰 <b>Цена:</b> {discount_price_str}\n"
            text += f"📉 <b>Было:</b> {original_price_str}\n"
            text += f"🎯 <b>Скидка:</b> <b>-{discount_percent}%</b>\n"
        else:
            text += f"💰 <b>Цена:</b> {price_str}\n"

        text += "\n"
        
        # Блок с рейтингом и отзывами
        if rating > 0:
            stars = "⭐" * min(5, int(rating))
            text += f"{stars} <b>Рейтинг:</b> {rating:.1f}/5.0\n"
        
        if reviews > 0:
            reviews_str = f"{reviews:,}".replace(',', ' ')
            text += f"📝 <b>Отзывов:</b> {reviews_str}\n"
        else:
            text += "📝 <b>Отзывов:</b> пока нет\n"
        
        # Блок с наличием
        if quantity is not None and quantity > 0:
            text += f"📦 <b>В наличии:</b> {quantity} шт.\n"
        elif is_available:
            text += "✅ <b>В наличии</b>\n"
        else:
            text += "❌ <b>Нет в наличии</b>\n"
        
        # Блок с навигацией и ссылкой
        text += f"🔢 <b>Товар {current_index + 1} из {total_count}</b>\n"
        
        # Проверяем, что URL не пустой
        if product_url and product_url.startswith('http'):
            text += f"🔗 <a href='{product_url}'>Перейти к товару на {platform_name}</a>\n\n"
        else:
            text += f"🔗 Ссылка на товар недоступна\n\n"
        
        # Добавляем хештеги
        hashtags = [platform_hashtag]
        if discount_price and discount_price < price:
            hashtags.append("#скидка")
        
        text += " ".join(hashtags)
        
        # Обрезаем если слишком длинный
        if len(text) > 1024:
            important_parts = [
                f"{platform_emoji} <b>{name}</b>\n\n",
                f"📋 <b>Артикул:</b> <code>{product_id}</code>\n",
                f"💰 <b>Цена:</b> {price_str}\n",
                f"📦 <b>В наличии:</b> {quantity} шт.\n" if quantity and quantity > 0 else "✅ <b>В наличии</b>\n",
                f"⭐ <b>Рейтинг:</b> {rating:.1f}/5.0\n" if rating > 0 else "",
            ]
            
            # Добавляем ссылку только если она валидна
            if product_url and product_url.startswith('http'):
                important_parts.append(f"🔗 <a href='{product_url}'>Перейти к товару</a>")
            
            text = "".join(important_parts)
            
            if len(text) > 1024:
                text = text[:1020] + "..."
        
        return text

    async def stats_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        products = context.user_data.get('last_results', [])
        query = context.user_data.get('query', 'неизвестный запрос')
        
        if not products:
            await update.message.reply_text(
                "❌ <b>Нет данных для статистики</b>\n\n"
                "🔍 Сначала выполните поиск товаров",
                parse_mode="HTML",
                reply_markup=self._get_main_keyboard()
            )
            return
        
        # Рассчитываем статистику
        prices = [p.get('price', 0) for p in products]
        discount_prices = [p.get('discount_price', 0) for p in products if p.get('discount_price')]
        ratings = [p.get('rating', 0) for p in products if p.get('rating', 0) > 0]
        
        avg_price = sum(prices) / len(prices) if prices else 0
        min_price = min(prices) if prices else 0
        max_price = max(prices) if prices else 0
        
        avg_rating = sum(ratings) / len(ratings) if ratings else 0
        
        discount_count = len(discount_prices)
        if discount_count > 0:
            avg_discount = sum((p.get('price', 0) - p.get('discount_price', 0)) / p.get('price', 1) * 100 
                          for p in products if p.get('discount_price')) / discount_count
        else:
            avg_discount = 0
        
        # Форматируем цены
        avg_price_str = f"{avg_price:,.0f} ₽".replace(',', ' ')
        min_price_str = f"{min_price:,.0f} ₽".replace(',', ' ')
        max_price_str = f"{max_price:,.0f} ₽".replace(',', ' ')
        
        # Формируем красивый текст статистики
        text = (
            f"📊 <b>СТАТИСТИКА ПО ЗАПРОСУ:</b> <code>{query}</code>\n\n"
            f"📦 <b>Товаров найдено:</b> <b>{len(products)}</b>\n\n"
            f"💰 <b>Средняя цена:</b> <b>{avg_price_str}</b>\n"
            f"📉 <b>Минимальная цена:</b> <b>{min_price_str}</b>\n"
            f"📈 <b>Максимальная цена:</b> <b>{max_price_str}</b>\n\n"
        )
        
        if discount_count > 0:
            text += (
                f"🎁 <b>Товаров со скидкой:</b> <b>{discount_count}</b>\n"
                f"💸 <b>Средняя скидка:</b> <b>{avg_discount:.1f}%</b>\n\n"
            )
        
        if ratings:
            text += f"⭐ <b>Средний рейтинг:</b> <b>{avg_rating:.1f}/5.0</b>\n"
        
        # Добавляем эмодзи в зависимости от результатов
        if avg_discount > 20:
            text += "\n🎯 <b>Отличные скидки!</b>"
        elif avg_rating > 4.0:
            text += "\n👍 <b>Высокий рейтинг товаров</b>"
        elif min_price < 1000:
            text += "\n💫 <b>Есть бюджетные варианты</b>"
        
        await update.message.reply_text(
            text,
            parse_mode="HTML",
            reply_markup=self._get_main_keyboard()
        )

    async def history_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Показ истории поиска из БД"""
        user = context.user_data.get('db_user')
        if not user:
            try:
                user, created = await self.user_service.get_or_create_telegram_user(update)
                context.user_data['db_user'] = user
            except Exception as e:
                logger.error(f"Ошибка получения пользователя для показа истории: {e}")
                await update.message.reply_text("❌ Ошибка загрузки истории.")
                return

        try:
            # ЗАГРУЖАЕМ ИСТОРИЮ ИЗ БАЗЫ ДАННЫХ
            user_stats = await self.user_service.get_user_stats(user.user_id)
            # Или напрямую через модель, если нужен детальный список:
            from asgiref.sync import sync_to_async
            from app.models import UserSearchHistory
            
            # Получаем последние N записей истории поиска для пользователя
            search_history_db = await sync_to_async(
                lambda: list(
                    UserSearchHistory.objects.filter(user=user).order_by('-created_at')[:10]
                )
            )()
            
            if not search_history_db:
                await update.message.reply_text(
                    "📝 <b>История поиска пуста</b>\n\n"
                    "🔍 Выполните поиск товаров, чтобы сохранить историю запросов",
                    parse_mode="HTML",
                    reply_markup=self._get_main_keyboard()
                )
                return

            # Формируем текст из данных БД
            text = "✨ <b>ИСТОРИЯ ПОИСКА (из БД)</b>\n\n"
            for i, history_item in enumerate(search_history_db, 1):
                query = history_item.query
                timestamp = history_item.created_at.strftime("%d.%m.%Y в %H:%M") # Форматируем дату из БД
                count = history_item.results_count
                platform_code = history_item.platform
                platform_name = "Wildberries 🛍️" if platform_code == 'WB' else "Ozon 🟠"
                
                text += f"🔍 <b>Запрос {i}:</b> <code>{query}</code>\n"
                text += f"   📦 Найдено товаров: <b>{count}</b>\n"
                text += f"   🏪 Платформа: {platform_name}\n"
                text += f"   🕒 Время: {timestamp}\n\n"
            
            text += "💡 <i>Нажмите на запрос ниже чтобы посмотреть товары</i>"
            
            # Создаем клавиатуру из запросов, полученных из БД
            keyboard = []
            for history_item in search_history_db[:5]:
                query = history_item.query
                display_query = query[:15] + "..." if len(query) > 18 else query
                keyboard.append([KeyboardButton(f"🔍 {display_query}")])
            
            keyboard.append([KeyboardButton("🧹 Очистить историю")])
            keyboard.append([KeyboardButton("↩️ Назад в меню")])
            
            reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
            
            await update.message.reply_text(
                text,
                parse_mode="HTML",
                reply_markup=reply_markup
            )
            
        except Exception as e:
            logger.error(f"Ошибка загрузки истории из БД: {e}")
            await update.message.reply_text("❌ Ошибка загрузки истории поиска.")

    async def _save_search_history(self, update: Update, context: ContextTypes.DEFAULT_TYPE, query: str, 
                             products: list, platform_name: str):
        """Сохранение истории поиска в БД с информацией о платформе"""
        
        if query in ["🔄 Вернуться к истории", "✅ Да, очистить историю", "❌ Нет, отменить"]:
            return
        
        # Получаем пользователя из context или создаем нового
        user = context.user_data.get('db_user')
        if not user:
            try:
                user, created = await self.user_service.get_or_create_telegram_user(update)
                if not user:
                    logger.error("Не удалось получить или создать пользователя")
                    return
                context.user_data['db_user'] = user
            except Exception as e:
                logger.error(f"Ошибка получения пользователя для сохранения истории: {e}")
                return
        
        # Проверяем, что user не None
        if user is None:
            logger.error("Объект пользователя равен None")
            return
        
        user_id = user.user_id
        results_count = len(products)
        
        # Сохраняем запись о поиске в базу данных
        try:
            # Используем код платформы вместо отображаемого имени
            platform_code = 'WB' if 'Wildberries' in platform_name else 'OZ'
            
            await self.user_service.save_search_history(
                user_id=user_id,
                query=query,
                platform=platform_code,
                results_count=results_count
            )
            logger.info(f"История поиска сохранена для user_id={user_id}, запрос='{query}'")
        except Exception as e:
            logger.error(f"Ошибка сохранения истории поиска в БД: {e}")
        
        # Сохраняем в локальный кеш для текущей сессии
        if 'search_history' not in context.user_data:
            context.user_data['search_history'] = []
        
        history = context.user_data['search_history']
        timestamp = datetime.now().strftime("%d.%m.%Y в %H:%M")
        
        # Удаляем старые записи с таким же запросом и платформой
        history = [item for item in history if not (
            item.get('query') == query and item.get('platform') == platform_name
        )]
        
        # Добавляем новую запись
        history.insert(0, {
            'query': query,
            'results_count': results_count,
            'products': products[:10], # Сохраняем продукты для контекста сессии
            'timestamp': timestamp,
            'platform': platform_name
        })
        
        # Ограничиваем историю
        context.user_data['search_history'] = history[:10]

    async def platform_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Команда для смены платформы"""
        await self.switch_platform(update, context)

    async def cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        await update.message.reply_text(
            "❌ Поиск отменен.",
            reply_markup=self._get_main_keyboard()
        )
        context.user_data['in_search'] = False
        return ConversationHandler.END

    async def debug_image(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Команда для отладки изображений"""
        products = context.user_data.get('last_results', [])
        
        if not products:
            await update.message.reply_text("❌ Нет данных для отладки.")
            return
            
        product = products[0]
        product_id = product.get('product_id', '')
        image_url = product.get('image_url')
        
        if not image_url:
            await update.message.reply_text("❌ Нет URL изображения.")
            return
            
        await update.message.reply_text(f"🔍 Текущий URL изображения: {image_url}")
        
        # Проверяем доступность
        is_available = await self._is_image_available(image_url)
        await update.message.reply_text(f"📡 Доступность: {'✅ Доступно' if is_available else '❌ Недоступно'}")
        
        if not is_available:
            # Ищем альтернативные изображения
            alternative_urls = await asyncio.to_thread(
                self.current_parser._generate_all_image_urls, 
                int(product_id) if product_id.isdigit() else 0
            )
            
            await update.message.reply_text(f"🔍 Всего альтернативных URL: {len(alternative_urls)}")
            
            # Проверяем первые 5 альтернативных URL
            found_working = False
            for i, alt_url in enumerate(alternative_urls[:5]):
                alt_available = await self._is_image_available(alt_url)
                status = "✅" if alt_available else "❌"
                await update.message.reply_text(f"{status} Альтернатива {i+1}: {alt_url}")
                
                if alt_available and not found_working:
                    # Пробуем отправить альтернативное изображение
                    try:
                        await update.message.reply_photo(
                            photo=alt_url,
                            caption="🔄 Альтернативное изображение (найдено автоматически)"
                        )
                        found_working = True
                        # Обновляем URL в продукте
                        product['image_url'] = alt_url
                        await update.message.reply_text("✅ Автоматически обновлен URL изображения!")
                    except Exception as e:
                        await update.message.reply_text(f"❌ Ошибка отправки альтернативы: {e}")
            
            if not found_working:
                await update.message.reply_text("❌ Не найдено рабочих альтернативных изображений")
        
        # Дополнительная диагностика
        try:
            # Проверяем размер изображения
            if is_available:
                img_data = await self._download_image(image_url)
                if img_data:
                    img_bytes, content_type = img_data
                    file_size = len(img_bytes.getvalue())
                    await update.message.reply_text(
                        f"📊 Размер изображения: {file_size} байт\n"
                        f"📝 Content-Type: {content_type}"
                    )
                    
                    # Пробуем разные методы отправки
                    await update.message.reply_text("🔄 Тестируем методы отправки...")
                    
                    # Метод 1: Прямая отправка по URL
                    try:
                        await update.message.reply_photo(
                            photo=image_url,
                            caption="📤 Метод 1: Прямая отправка по URL"
                        )
                        await update.message.reply_text("✅ Метод 1: Успешно!")
                    except Exception as e:
                        await update.message.reply_text(f"❌ Метод 1: Ошибка - {e}")
                    
                    # Метод 2: Загрузка и отправка
                    try:
                        await update.message.reply_photo(
                            photo=img_bytes,
                            caption="📥 Метод 2: Загрузка и отправка"
                        )
                        await update.message.reply_text("✅ Метод 2: Успешно!")
                    except Exception as e:
                        await update.message.reply_text(f"❌ Метод 2: Ошибка - {e}")
                        
        except Exception as e:
            await update.message.reply_text(f"❌ Ошибка диагностики: {e}")
        
        # Предлагаем варианты решения
        solution_text = (
            "💡 <b>Возможные решения:</b>\n\n"
            "1. 🕐 Попробовать позже - возможно временные проблемы с сервером\n"
            "2. 🔄 Выполнить поиск заново для обновления изображений\n"
            "3. 📝 Использовать другой поисковый запрос\n"
            "4. ⚙️ Проверить настройки бота и парсера"
        )
        
        await update.message.reply_text(solution_text, parse_mode="HTML")
    
    async def debug_ozon(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Отладочная команда для проверки Ozon"""
        test_queries = ["телефон", "ноутбук", "кроссовки", "книга"]
        
        for query in test_queries:
            await update.message.reply_text(f"🔍 Тестируем Ozon по запросу: {query}")
            
            try:
                products = await asyncio.to_thread(self.parsers['OZ'].search_products, query, 3)
                
                if products:
                    await update.message.reply_text(
                        f"✅ Успешно! Найдено {len(products)} товаров\n"
                        f"Пример: {products[0].get('name', 'Без названия')}"
                    )
                else:
                    await update.message.reply_text("❌ Товары не найдены")
                    
            except Exception as e:
                await update.message.reply_text(f"❌ Ошибка: {str(e)}")
            
            await asyncio.sleep(1)
    
    async def check_ozon_api(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Проверка доступности Ozon API"""
        try:
            test_url = "https://www.ozon.ru/api/entrypoint-api.bx/page/json/v2"
            payload = {
                "url": "/search/?text=телефон",
                "params": {"text": "телефон"}
            }
            
            response = requests.post(
                test_url,
                json=payload,
                headers={
                    'User-Agent': self.ua.random,
                    'Content-Type': 'application/json',
                    'Origin': 'https://www.ozon.ru',
                    'Referer': 'https://www.ozon.ru/'
                },
                timeout=10
            )
            
            if response.status_code == 200:
                await update.message.reply_text(
                    f"✅ Ozon API доступен\n"
                    f"Status: {response.status_code}\n"
                    f"Response: {response.text[:200]}..."
                )
            else:
                await update.message.reply_text(
                    f"❌ Ozon API недоступен\n"
                    f"Status: {response.status_code}\n"
                    f"Response: {response.text[:200]}..."
                )
                
        except Exception as e:
            await update.message.reply_text(f"❌ Ошибка проверки Ozon API: {str(e)}")

class Command(BaseCommand):
    help = 'Запускает Telegram бота для парсинга Wildberries'

    def handle(self, *args, **options):
        token = "8124289862:AAGPVxgf5gyphHU1SUwVfgozwbEL9a1NO24"
        
        bot = MultiPlatformBot(token)
        application = Application.builder().token(token).build()
        
        # Добавляем обработчики команд
        application.add_handler(CommandHandler("start", bot.start))
        application.add_handler(CommandHandler("help", bot.help))
        application.add_handler(CommandHandler("stats", bot.stats_command))
        application.add_handler(CommandHandler("history", bot.history_command))
        application.add_handler(CommandHandler("platform", bot.platform_command))
        application.add_handler(CommandHandler("debug", bot.debug_image))
        application.add_handler(CommandHandler("top", bot.top_products))
        application.add_handler(CommandHandler("debug_image", bot.debug_image))
        application.add_handler(CommandHandler("checkdb", bot.check_db))
        application.add_handler(MessageHandler(filters.Document.ALL, bot.handle_media))
        application.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO, bot.handle_media))
        application.add_handler(MessageHandler(filters.LOCATION | filters.POLL, bot.handle_media))
     
         # Добавляем ConversationHandler для поиска
        conv_handler = ConversationHandler(
            entry_points=[
                CommandHandler("search", bot.search_command),
                MessageHandler(filters.Regex("^🔍 Поиск товаров$"), bot.search_command)
            ],
            states={
                SEARCH_QUERY: [MessageHandler(filters.TEXT & ~filters.COMMAND, bot.receive_query)],
                SEARCH_LIMIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, bot.receive_limit)],
            },
            fallbacks=[
                CommandHandler("cancel", bot.cancel),
                MessageHandler(filters.Regex("^❌ Отменить поиск$"), bot.cancel_search),
            ],
        )
        application.add_handler(conv_handler)
        
        # Добавляем обработчик текстовых сообщений
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_message))
        
        self.stdout.write(self.style.SUCCESS('Мультиплатформенный бот запущен и работает...'))
        
        # Создаем новое событийное loop для асинхронных операций
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            # Инициализируем бота
            loop.run_until_complete(bot.init_session())
            
            # Запускаем бота в отдельной задаче
            bot_task = loop.create_task(application.run_polling())
            
            # Ждем завершения работы бота
            loop.run_until_complete(bot_task)
            
        except KeyboardInterrupt:
            self.stdout.write(self.style.WARNING('Бот остановлен пользователем'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Ошибка: {e}'))
        finally:
            try:
                # Корректно закрываем сессии
                if not loop.is_closed():
                    close_task = loop.create_task(bot.close_session())
                    loop.run_until_complete(asyncio.wait_for(close_task, timeout=5.0))
            except Exception as e:
                self.stdout.write(self.style.ERROR(f'Ошибка при закрытии: {e}'))
            finally:
                # Всегда закрываем loop
                try:
                    if not loop.is_closed():
                        loop.close()
                except:
                    pass