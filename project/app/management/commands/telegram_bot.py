import asyncio
import logging
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
from app.wildberries_parser import WildberriesParser
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import aiohttp

class IgnoreUnicodeErrorsHandler(logging.StreamHandler):
    def emit(self, record):
        try:
            super().emit(record)
        except UnicodeEncodeError:
            pass  # Просто игнорируем ошибки Unicode

# Настройка логгера
logger = logging.getLogger(__name__)
logger.addHandler(IgnoreUnicodeErrorsHandler())
logger.setLevel(logging.INFO)

# Состояния для ConversationHandler
SEARCH_QUERY, SEARCH_CATEGORY, SEARCH_LIMIT = range(3)

class WildberriesBot:
    def __init__(self, token: str):
        self.token = token
        self.parser = WildberriesParser()
        self.executor = ThreadPoolExecutor(max_workers=5)
        self.session = None
    
    async def init_session(self):
        """Инициализация сессии в асинхронном контексте"""
        if self.session is None:
            self.session = aiohttp.ClientSession()
    
    async def close_session(self):
        """Закрытие сессии"""
        if self.session:
            await self.session.close()
            self.session = None

    def _get_main_keyboard(self):
        """Основная клавиатура с кнопками"""
        keyboard = [
            [KeyboardButton("🔍 Поиск товаров")],
            [KeyboardButton("🔄 История поиска"), KeyboardButton("ℹ️ Помощь")],
            [KeyboardButton("🎯 Топ товаров"), KeyboardButton("💎 Акции")]
        ]
        return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, input_field_placeholder="Выберите действие...")

    def _get_search_keyboard(self):
        """Клавиатура для поиска"""
        keyboard = [
            [KeyboardButton("5 товаров"), KeyboardButton("10 товаров")],
            [KeyboardButton("15 товаров"), KeyboardButton("20 товаров")],
            [KeyboardButton("↩️ Назад в меню")]
        ]
        return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, input_field_placeholder="Выберите количество...")
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        welcome_text = (
            "🎉 <b>Добро пожаловать в Wildberries Bot!</b>\n\n"
            "Я помогу вам найти лучшие товары на Wildberries.\n\n"
            "✨ <b>Как пользоваться:</b>\n"
            "• Используйте <b>кнопки меню</b> ниже\n"
            "• Или введите <b>название товара</b> для поиска\n"
            "• Команда <code>/help</code> - список всех команд\n\n"
            "❌ <b>Не поддерживается:</b> файлы, фото, видео, геопозиция\n\n"
            "💡 <b>Начните с кнопок меню или введите товар для поиска!</b>"
        )
        
        await update.message.reply_text(
            welcome_text, 
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
            "• /help - показать это сообщение\n\n"
            
            "⚡ <b>Быстрые действия:</b>\n"
            "• Просто напишите название товара для быстрого поиска!\n"
            "• Используйте кнопки ниже для удобной навигации\n\n"
            
            "💎 <b>Доступные кнопки:</b>\n"
            "• 🔍 Поиск товаров - расширенный поиск\n"
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
    
    async def commands_list(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Отдельная команда для списка всех доступных команд"""
        commands_text = (
            "⌨️ <b>ДОСТУПНЫЕ КОМАНДЫ</b>\n\n"
            
            "<b>Текстовые команды:</b>\n"
            "<code>/start</code> - начать работу\n"
            "<code>/search</code> - расширенный поиск\n"
            "<code>/history</code> - история\n"
            "<code>/top</code> - топ товаров\n"
            "<code>/help</code> - помощь\n"
            "<code>/commands</code> - этот список\n\n"
            
            "<b>Быстрый поиск:</b>\n"
            "Просто напишите название товара в чат!\n\n"
            
            "<b>Кнопки меню:</b>\n"
            "Используйте кнопки ниже для быстрой навигации"
        )
        
        await update.message.reply_text(
            commands_text,
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
        
        # Сначала проверяем ВСЕ возможные кнопки
        if text == "🔍 Поиск товаров":
            await self.search_command(update, context)
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
        elif text == "↩️ Назад в меню":
            await self.history_command(update, context)
        elif context.user_data.get('awaiting_confirmation', False):
            # Если ждем подтверждения очистки
            await self.handle_confirmation(update, context)
        elif text.startswith("🔍 ") and len(text) > 2:
            # Обработка клика по истории поиска
            query = text[2:].strip()
            await self.show_history_products(update, context, query)
        else:
            # Если это не кнопка, проверяем состояние разговора
            if context.user_data.get('in_search', False):
                # Если мы в процессе поиска, передаем управление соответствующим обработчикам
                current_state = context.user_data.get('search_state')
                if current_state == SEARCH_QUERY:
                    await self.receive_query(update, context)
                elif current_state == SEARCH_LIMIT:
                    await self.receive_limit(update, context)
            else:
                # Обычный текстовый запрос - быстрый поиск
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
        text = update.message.text.strip()
        
        if text == "✅ Да, очистить историю":
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
            self.parser.search_products_with_strategy,
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
        await update.message.reply_text(
            "🔍 <b>Расширенный поиск</b>\n\n"
            "Введите название товара для поиска:",
            parse_mode="HTML",
            reply_markup=ReplyKeyboardMarkup([[KeyboardButton("↩️ Назад в меню")]], 
                                        resize_keyboard=True)
        )
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
        text = update.message.text.strip()
        
        # Проверяем, не является ли это кнопкой "Назад"
        if text == "↩️ Назад в меню":
            await update.message.reply_text(
                "Возвращаемся в главное меню...",
                reply_markup=self._get_main_keyboard()
            )
            return ConversationHandler.END
        
        query = text.strip()
        if len(query) < 2:
            await update.message.reply_text("❌ Слишком короткий запрос. Попробуйте еще раз.")
            return SEARCH_QUERY
        
        context.user_data['query'] = query
        
        # Теперь предлагаем выбрать количество товаров
        keyboard = [
            [KeyboardButton("5 товаров"), KeyboardButton("10 товаров")],
            [KeyboardButton("15 товаров"), KeyboardButton("20 товаров")],
            [KeyboardButton("↩️ Назад в меню")]
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await update.message.reply_text(
            f"🔍 Вы ищете: <b>{query}</b>\n\nТеперь выберите количество товаров:",
            parse_mode="HTML",
            reply_markup=reply_markup
        )
        return SEARCH_LIMIT

    async def receive_limit(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        text = update.message.text.strip()

        if text not in ["5 товаров", "10 товаров", "15 товаров", "20 товаров"]:
            # Если это не кнопка количества, возвращаемся к обработке сообщений
            await self.handle_message(update, context)
            return ConversationHandler.END
        
        # Обрабатываем кнопки количества товаров
        if text == "5 товаров":
            limit = 5
        elif text == "10 товаров":
            limit = 10
        elif text == "15 товаров":
            limit = 15
        elif text == "20 товаров":
            limit = 20
        elif text == "↩️ Назад в меню":
            await update.message.reply_text(
                "Возвращаемся к выбору категории...",
                reply_markup=self._get_search_keyboard()
            )
            return SEARCH_QUERY
        else:
            try:
                limit = int(text)
                if limit < 1 or limit > 20:
                    raise ValueError
            except ValueError:
                await update.message.reply_text("❌ Некорректное число. Введите число от 1 до 20.")
                return SEARCH_LIMIT
        
        query = context.user_data['query']
        
        search_msg = await update.message.reply_text(
            f"🔍 <b>Ищу {limit} товаров по запросу:</b> <code>{query}</code>\n\n"
            "⏳ Это может занять несколько секунд...",
            parse_mode="HTML"
        )
        
        try:
            # Используем новый метод search_products
            products_data = await asyncio.to_thread(self.parser.search_products, query, limit)
            
            if not products_data:
                await search_msg.edit_text(
                    f"❌ <b>По запросу</b> <code>{query}</code> <b>ничего не найдено</b>\n\n"
                    "💡 Попробуйте изменить запрос",
                    parse_mode="HTML"
                )
                return ConversationHandler.END
            
            # Сохраняем товары асинхронно
            saved_count = await self.parser.parse_and_save_async(query, limit)
            
            if saved_count == 0:
                await search_msg.edit_text(
                    "❌ <b>Не удалось сохранить товары</b>\n\n"
                    "⚠️ Попробуйте другой запрос",
                    parse_mode="HTML"
                )
                return ConversationHandler.END
            
            # Получаем сохраненные товары из базы ПО ID
            from app.models import Product
            
            # Получаем ID из products_data
            product_ids = [str(p.get('product_id')) for p in products_data if p.get('product_id')]
            logger.info("Ищем товары с ID: %s", product_ids)
            
            if product_ids:
                products = await asyncio.to_thread(
                    lambda: list(Product.objects.filter(product_id__in=product_ids))
                )
                logger.info("Найдено в базе: %s товаров", len(products))
            else:
                # Fallback: берем последние товары
                products = await asyncio.to_thread(
                    lambda: list(Product.objects.all().order_by('-id')[:limit])
                )
                logger.info("Взяли последние: %s товаров", len(products))
            
            # ОТЛАДОЧНАЯ ИНФОРМАЦИЯ
            if products:
                for p in products:
                    logger.info("Товар для отправки: %s - %s (image: %s)", 
                            p.product_id, p.name, bool(p.image_url))
            else:
                logger.warning("Нет товаров для отправки!")
            
            # Сохраняем в context для статистики
            context.user_data['last_results'] = products
            context.user_data['query'] = query
            
            await search_msg.edit_text(
                f"✅ <b>Найдено и сохранено {saved_count} товаров</b>\n\n"
                "📦 Отправляю результаты...",
                parse_mode="HTML"
            )
            
            # ПРЕОБРАЗУЕМ ДЛЯ ОТПРАВКИ
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
                    'is_available': product.is_available
                })
            
            logger.info("Подготовлено для отправки: %s товаров", len(products_for_sending))
            
            # СОХРАНЯЕМ ИСТОРИЮ ПОИСКА
            await self._save_search_history(context, query, products_for_sending)
            
            # ОТПРАВЛЯЕМ ТОВАРЫ
            await self.send_all_products(update, products_for_sending)
            
        except Exception as e:
            logger.error("Ошибка поиска: %s", str(e), exc_info=True)
            await search_msg.edit_text(
                "⚠️ <b>Произошла ошибка при поиске</b>\n\n"
                "🔧 Пожалуйста, попробуйте позже",
                parse_mode="HTML"
            )
            return ConversationHandler.END  # Добавляем возврат здесь
        
        await update.message.reply_text(
            "🎉 <b>Поиск завершен!</b>\n\n"
            "💡 Используйте кнопки для новых запросов:",
            parse_mode="HTML",
            reply_markup=self._get_main_keyboard()
        )
        return ConversationHandler.END

    async def quick_search(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:

        query = update.message.text.strip()
        text = update.message.text.strip()
    
        # ПРОВЕРЯЕМ ЧТО ЭТО НЕ КНОПКА
        if text in ["↩️ Назад", "↩️ Назад в меню", "🔄 Вернуться к истории", 
                    "✅ Да, очистить историю", "❌ Нет, отменить"]:
            # Если это кнопка, а не поисковый запрос
        
            logger.info("Быстрый поиск по запросу: '%s'", text)
            logger.info("Быстрый поиск по запросу: '%s'", query)
        
        if len(query) < 2:
            await update.message.reply_text("❌ Слишком короткий запрос. Попробуйте еще раз.")
            return
        
        search_msg = await update.message.reply_text(
            f"🔍 <b>Ищу товары по запросу:</b> <code>{query}</code>\n\n"
            "⏳ Это может занять несколько секунд...",
            parse_mode="HTML"
        )
        
        try:
            # Анимируем процесс поиска
            dots = ["", ".", "..", "..."]
            for i in range(3):
                try:
                    await search_msg.edit_text(
                        f"🔍 <b>Ищу товары по запросу:</b> <code>{query}</code>{dots[i % 4]}\n\n"
                        "⏳ Это может занять несколько секунд...",
                        parse_mode="HTML"
                    )
                except Exception:
                    pass
                await asyncio.sleep(0.5)
            
            # Ищем товары
            raw_products = await asyncio.to_thread(self.parser.search_products, query, 10)
            logger.info("Получено %s сырых товаров", len(raw_products))
            
            if not raw_products:
                await search_msg.edit_text(
                    f"❌ <b>По запросу</b> <code>{query}</code> <b>ничего не найдено</b>\n\n"
                    "💡 Попробуйте изменить запрос",
                    parse_mode="HTML"
                )
                return
            
            # Сохраняем товары
            saved_count = await self.parser.parse_and_save_async(query, 10)
            
            if saved_count == 0:
                await search_msg.edit_text(
                    "❌ <b>Не удалось сохранить товары</b>\n\n"
                    "⚠️ Попробуйте другой запрос",
                    parse_mode="HTML"
                )
                return
            
            # ПОЛУЧАЕМ ТОВАРЫ ИЗ БАЗЫ ПРАВИЛЬНО
            from app.models import Product
            
            # Вариант 1: По ID из сырых данных
            product_ids = []
            for p in raw_products:
                pid = p.get('product_id')
                if pid:
                    product_ids.append(str(pid))
            
            if product_ids:
                products = await asyncio.to_thread(
                    lambda: list(Product.objects.filter(product_id__in=product_ids))
                )
                logger.info("Найдено по ID: %s товаров", len(products))
            else:
                # Вариант 2: Последние товары
                products = await asyncio.to_thread(
                    lambda: list(Product.objects.all().order_by('-id')[:10])
                )
                logger.info("Найдено последних: %s товаров", len(products))
            
            if not products:
                await search_msg.edit_text(
                    "❌ <b>Не удалось загрузить товары из базы</b>\n\n"
                    "⚠️ Попробуйте еще раз",
                    parse_mode="HTML"
                )
                return
            
            # Отладочная информация
            logger.info("Товары для отправки: %s", len(products))
            for p in products:
                logger.info(" - %s: %s", p.product_id, p.name)
            
            context.user_data['last_results'] = products
            context.user_data['query'] = query
            
            await search_msg.edit_text(
                f"✅ <b>Найдено и сохранено {saved_count} товаров</b>\n\n"
                "📦 Отправляю результаты...",
                parse_mode="HTML"
            )
            
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
                    'is_available': product.is_available
                })
            
            # ТЕПЕРЬ СОХРАНЯЕМ ИСТОРИЮ ПОСЛЕ ТОГО КАК products_for_sending СОЗДАН
            await self._save_search_history(context, query, products_for_sending)
            
            # Отправляем товары
            await self.send_all_products(update, products_for_sending)
            
        except Exception as e:
            logger.error("Ошибка поиска: %s", str(e), exc_info=True)
            await search_msg.edit_text(
                "⚠️ <b>Произошла ошибка при поиске</b>\n\n"
                "🔧 Пожалуйста, попробуйте позже",
                parse_mode="HTML"
            )

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
                self.parser._generate_all_image_urls, 
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
        """Генерация красивой подписи для товара"""
        name = product.get('name', 'Без названия')
        price = product.get('price', 0)
        discount_price = product.get('discount_price')
        wb_card_price = product.get('wildberries_card_price')
        rating = product.get('rating', 0)
        reviews = product.get('reviews_count', 0)
        product_url = product.get('product_url', '')
        has_wb_card_discount = product.get('has_wb_card_discount', False)
        product_id = product.get('product_id', 'N/A')
        quantity = product.get('quantity', 0)
        is_available = product.get('is_available', False)
        
        # Форматируем цены
        price_str = f"<b>{price:,.0f} ₽</b>".replace(',', ' ')
        
        # Создаем красивый текст
        text = f"🏷️ <b>{name}</b>\n\n"
        
        # Блок с артикулом
        text += f"📋 <b>Артикул:</b> <code>{product_id}</code>\n"
        
        # Блок с ценами
        if discount_price and discount_price < price:
            # Товар со скидкой
            discount_percent = int((1 - discount_price / price) * 100)
            discount_price_str = f"<b>{discount_price:,.0f} ₽</b>".replace(',', ' ')
            original_price_str = f"<s>{price:,.0f} ₽</s>".replace(',', ' ')
            
            text += f"💰 <b>Цена:</b> {discount_price_str}\n"
            text += f"📉 <b>Было:</b> {original_price_str}\n"
            text += f"🎯 <b>Скидка:</b> <b>-{discount_percent}%</b>\n"
            
            if has_wb_card_discount and wb_card_price:
                wb_card_str = f"<b>{wb_card_price:,.0f} ₽</b>".replace(',', ' ')
                text += f"💳 <b>По карте WB:</b> {wb_card_str}\n"
                
        else:
            # Обычная цена
            text += f"💰 <b>Цена:</b> {price_str}\n"
            if has_wb_card_discount and wb_card_price:
                wb_card_str = f"<b>{wb_card_price:,.0f} ₽</b>".replace(',', ' ')
                text += f"💳 <b>По карте WB:</b> {wb_card_str}\n"

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
        
        # Блок с наличием товара - УПРОЩЕННАЯ ЛОГИКА
        if quantity is not None and quantity > 0:
            text += f"📦 <b>В наличии:</b> {quantity} шт.\n"
        elif is_available:
            text += "✅ <b>В наличии</b>\n"
        else:
            text += "❌ <b>Нет в наличии</b>\n"
        
        # Блок с навигацией и ссылкой
        text += f"🔢 <b>Товар {current_index + 1} из {total_count}</b>\n"
        text += f"🔗 <a href='{product_url}'>Перейти к товару на Wildberries</a>\n\n"
        
        # Добавляем хештеги
        hashtags = ["#wildberries"]
        if discount_price and discount_price < price:
            hashtags.append("#скидка")
        if has_wb_card_discount:
            hashtags.append("#картаWB")
        
        text += " ".join(hashtags)
        
        # Обрезаем если слишком длинный
        if len(text) > 1024:
            # Сохраняем самое важное
            important_parts = [
                f"🏷️ <b>{name}</b>\n\n",
                f"📋 <b>Артикул:</b> <code>{product_id}</code>\n",
                f"💰 <b>Цена:</b> {price_str}\n",
                f"📦 <b>В наличии:</b> {quantity} шт.\n" if quantity and quantity > 0 else "✅ <b>В наличии</b>\n",
                f"⭐ <b>Рейтинг:</b> {rating:.1f}/5.0\n" if rating > 0 else "",
                f"🔗 <a href='{product_url}'>Перейти к товару</a>"
            ]
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
        """Красивый показ истории поиска"""
        search_history = context.user_data.get('search_history', [])
    
        # Фильтруем историю - убираем записи с командами вместо запросов
        filtered_history = []
        for item in search_history:
            query = item.get('query', '')
            # Пропускаем записи где query это команда
            if query not in ["🔄 Вернуться к истории", "✅ Да, очистить историю", "❌ Нет, отменить"]:
                filtered_history.append(item)
        
        context.user_data['search_history'] = filtered_history[:15]  # Обновляем историю
        
        if not filtered_history:
            await update.message.reply_text(
                "📝 <b>История поиска пуста</b>\n\n"
                "🔍 Выполните поиск товаров, чтобы сохранить историю запросов",
                parse_mode="HTML",
                reply_markup=self._get_main_keyboard()
            )
            return
        
        if not search_history:
            await update.message.reply_text(
                "📝 <b>История поиска пуста</b>\n\n"
                "🔍 Выполните поиск товаров, чтобы сохранить историю запросов",
                parse_mode="HTML",
                reply_markup=self._get_main_keyboard()
            )
            return
        
        # Сортируем историю по времени (новые сначала)
        sorted_history = sorted(search_history, 
                            key=lambda x: x.get('timestamp', ''), 
                            reverse=True)
        
        # Формируем красивый текст
        text = "✨ <b>ИСТОРИЯ ПОИСКА</b>\n\n"
        
        for i, history_item in enumerate(sorted_history[:10], 1):  # Последние 10 запросов
            query = history_item.get('query', 'Неизвестно')
            timestamp = history_item.get('timestamp', '')
            count = history_item.get('results_count', 0)
            
            text += f"🔍 <b>Запрос {i}:</b> <code>{query}</code>\n"
            text += f"   📦 Найдено товаров: <b>{count}</b>\n"
            text += f"   🕒 Время: {timestamp}\n"
            
            # Добавляем разделитель между запросами
            if i < min(10, len(sorted_history)):
                text += "   ───────────────────\n"
            text += "\n"
        
        text += "💡 <i>Нажмите на запрос ниже чтобы посмотреть товары</i>"
        
        # Создаем клавиатуру с кнопками запросов
        keyboard = []
        for history_item in sorted_history[:5]:  # Первые 5 запросов
            query = history_item.get('query', '')
            # Обрезаем длинные запросы
            display_query = query[:18] + "..." if len(query) > 21 else query
            keyboard.append([KeyboardButton(f"🔍 {display_query}")])
        
        keyboard.append([KeyboardButton("🧹 Очистить историю")])
        keyboard.append([KeyboardButton("↩️ Назад в меню")])
        
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await update.message.reply_text(
            text,
            parse_mode="HTML",
            reply_markup=reply_markup
        )

    async def _save_search_history(self, context: ContextTypes.DEFAULT_TYPE, query: str, products: list):
        """Сохранение истории поиска с проверкой"""
        # Проверяем что query не является командой
        if query in ["🔄 Вернуться к истории", "✅ Да, очистить историю", "❌ Нет, отменить"]:
            return  # Не сохраняем команды в историю
        
        if 'search_history' not in context.user_data:
            context.user_data['search_history'] = []
        
        history = context.user_data['search_history']
        
        # Форматируем время
        from datetime import datetime
        timestamp = datetime.now().strftime("%d.%m.%Y в %H:%M")
        
        # Удаляем старые записи с таким же запросом
        history = [item for item in history if item.get('query') != query]
        
        # Добавляем новую запись
        history.insert(0, {
            'query': query,
            'results_count': len(products),
            'products': products[:10],
            'timestamp': timestamp
        })
        
        # Ограничиваем историю 15 записями
        context.user_data['search_history'] = history[:30]

    async def cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        await update.message.reply_text(
            "❌ Поиск отменен.",
            reply_markup=self._get_main_keyboard()
        )
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
                self.parser._generate_all_image_urls, 
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

class Command(BaseCommand):
    help = 'Запускает Telegram бота для парсинга Wildberries'

    def handle(self, *args, **options):
        token = "8124289862:AAGPVxgf5gyphHU1SUwVfgozwbEL9a1NO24"
        
        bot = WildberriesBot(token)
        application = Application.builder().token(token).build()
        
        # Добавляем обработчики команд
        application.add_handler(CommandHandler("start", bot.start))
        application.add_handler(CommandHandler("help", bot.help))
        application.add_handler(CommandHandler("stats", bot.stats_command))
        application.add_handler(CommandHandler("history", bot.history_command))
        application.add_handler(CommandHandler("debug", bot.debug_image))
        application.add_handler(CommandHandler("top", bot.top_products))
        application.add_handler(CommandHandler("debug_image", bot.debug_image))
        application.add_handler(CommandHandler("checkdb", bot.check_db))
        application.add_handler(MessageHandler(filters.Document.ALL, bot.handle_media))
        application.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO, bot.handle_media))
        application.add_handler(MessageHandler(filters.LOCATION | filters.POLL, bot.handle_media))
        application.add_handler(CommandHandler("commands", bot.commands_list))
        # Добавляем ConversationHandler для поиска
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
            fallbacks=[CommandHandler("cancel", bot.cancel)],
)
        application.add_handler(conv_handler)
        
        # Добавляем обработчик текстовых сообщений (кнопки и быстрый поиск)
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_message))
        
        self.stdout.write(self.style.SUCCESS('Бот запущен и работает...'))
        
        # Создаем новое событийное loop для асинхронных операций
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            loop.run_until_complete(bot.init_session())
            application.run_polling()
            
        except KeyboardInterrupt:
            self.stdout.write(self.style.WARNING('Бот остановлен пользователем'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Ошибка: {e}'))
        finally:
            loop.run_until_complete(bot.close_session())
            loop.close()