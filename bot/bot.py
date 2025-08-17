import logging
from typing import Dict, List
from functools import lru_cache
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
    CallbackContext,
)

from project.app.parser import WildberriesParser  # Используем абсолютный импорт

# Настройка логирования
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    filename="wb_bot.log",
)
logger = logging.getLogger(__name__)

class WBBot:
    def __init__(self, token: str):
        self.token = token
        self.parser = WildberriesParser()
        self.user_searches: Dict[int, List[Dict]] = {}  # Кэш поисковых запросов пользователей
        
        # Инициализация бота
        self.application = Application.builder().token(self.token).build()
        
        # Регистрация обработчиков
        self.register_handlers()
        
    def register_handlers(self):
        """Регистрирует все обработчики команд и сообщений"""
        handlers = [
            CommandHandler("start", self.start),
            CommandHandler("help", self.help),
            CommandHandler("search", self.search_command),
            CommandHandler("stats", self.show_stats),
            CommandHandler("history", self.show_history),
            MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_text),
            CallbackQueryHandler(self.button_callback),
        ]
        
        for handler in handlers:
            self.application.add_handler(handler)
        
        # Обработчик ошибок
        self.application.add_error_handler(self.error_handler)
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /start"""
        user = update.effective_user
        text = (
            f"👋 Привет, {user.first_name}!\n\n"
            "Я бот для поиска товаров на Wildberries.\n"
            "Просто отправь мне название товара, например:\n"
            "<code>кроссовки Nike</code>\n\n"
            "Или используй команду /search <i>запрос</i>\n\n"
            "Для справки используй /help"
        )
        await update.message.reply_text(text, parse_mode="HTML")
        
        # Сохраняем пользователя
        self.save_user(update.effective_user)
    
    async def help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /help"""
        text = (
            "📚 <b>Справка по использованию бота</b>\n\n"
            "🔍 <b>Поиск товаров:</b>\n"
            "- Просто напиши в чат название товара\n"
            "- Или используй /search <i>запрос</i>\n\n"
            "🔢 <b>Количество товаров:</b>\n"
            "Можно указать количество после запроса, например:\n"
            "<code>телефон 5</code> - найдет 5 телефонов\n\n"
            "📊 <b>Статистика:</b>\n"
            "После поиска используй /stats для просмотра статистики\n\n"
            "🕒 <b>История:</b>\n"
            "/history - показывает последние 10 запросов"
        )
        await update.message.reply_text(text, parse_mode="HTML")
    
    async def search_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /search"""
        query = " ".join(context.args)
        if not query:
            await update.message.reply_text("ℹ️ Укажите поисковый запрос. Например: /search iPhone 13")
            return
        
        await self.process_search(update, query, context)
    
    async def handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик текстовых сообщений"""
        query = update.message.text.strip()
        await self.process_search(update, query, context)
    
    async def process_search(self, update: Update, query: str, context: CallbackContext):
        """Обрабатывает поисковый запрос"""
        try:
            # Показываем статус "печатает..."
            await update.message.reply_chat_action("typing")
            
            # Парсим запрос и количество товаров
            query_parts = query.split()
            limit = 10
            
            if len(query_parts) > 1 and query_parts[-1].isdigit():
                limit = min(int(query_parts[-1]), 20)
                query = " ".join(query_parts[:-1])
            
            # Ищем товары (с кэшированием)
            products = self.search_with_cache(query, limit)
            
            if not products:
                await update.message.reply_text("😕 Ничего не найдено. Попробуйте изменить запрос.")
                return
            
            # Сохраняем поиск в истории
            self.save_search(update.effective_user.id, query, products)
            
            # Сохраняем результаты в context для статистики
            context.user_data['last_search'] = products
            
            # Отправляем первый товар
            await self.send_product(update, products, 0)
            
        except Exception as e:
            logger.error(f"Ошибка при обработке запроса '{query}': {e}", exc_info=True)
            await update.message.reply_text("⚠️ Произошла ошибка при поиске. Попробуйте позже.")
    
    @lru_cache(maxsize=100)
    def search_with_cache(self, query: str, limit: int = 10) -> List[Dict]:
        """Поиск товаров с кэшированием результатов"""
        return self.parser.search_products(query, limit)
    
    async def send_product(self, update: Update, products: List[Dict], index: int):
        """Отправляет информацию о товаре с кнопками навигации"""
        product = products[index]
        
        # Формируем текст сообщения
        text = self.format_product_info(product, index, len(products))
        
        # Создаем клавиатуру
        reply_markup = self.create_product_keyboard(index, len(products), product['product_url'])
        
        # Отправляем сообщение
        try:
            if product.get('image_url'):
                await update.message.reply_photo(
                    photo=product['image_url'],
                    caption=text,
                    parse_mode="HTML",
                    reply_markup=reply_markup
                )
                return
        except Exception as e:
            logger.warning(f"Не удалось отправить фото: {e}")
        
        # Если фото не отправилось, отправляем только текст
        await update.message.reply_text(
            text,
            parse_mode="HTML",
            reply_markup=reply_markup,
            disable_web_page_preview=True
        )
    
    def format_product_info(self, product: Dict, index: int, total: int) -> str:
        """Форматирует информацию о товаре для сообщения"""
        price_info = f"💰 <b>{product['price']} ₽</b>"
        if product.get('discount_price'):
            price_info += f" (⬇️ <b>{product['discount_price']} ₽</b>)"
        
        return (
            f"🛍️ <b>{product['name']}</b>\n\n"
            f"{price_info}\n"
            f"⭐ Рейтинг: {product['rating']} ({product['reviews_count']} отзывов)\n"
            f"🔗 <a href='{product['product_url']}'>Ссылка на Wildberries</a>\n\n"
            f"📌 Товар {index + 1} из {total}"
        )
    
    def create_product_keyboard(self, index: int, total: int, product_url: str) -> InlineKeyboardMarkup:
        """Создает клавиатуру для навигации по товарам"""
        buttons = []
        
        # Кнопки навигации
        if index > 0:
            buttons.append(InlineKeyboardButton("⬅️ Назад", callback_data=f"prev_{index}"))
        
        buttons.append(InlineKeyboardButton(f"{index + 1}/{total}", callback_data="info"))
        
        if index < total - 1:
            buttons.append(InlineKeyboardButton("Дальше ➡️", callback_data=f"next_{index}"))
        
        # Дополнительные кнопки
        other_buttons = [
            InlineKeyboardButton("📊 Статистика", callback_data="stats"),
            InlineKeyboardButton("🛒 На WB", url=product_url),
        ]
        
        return InlineKeyboardMarkup([buttons, other_buttons])
    
    async def button_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик нажатий на inline-кнопки"""
        query = update.callback_query
        await query.answer()
        
        data = query.data
        
        if data == "stats":
            await self.show_stats_callback(query, context)
        elif data.startswith(("prev_", "next_")):
            await self.handle_navigation(query, data)
    
    async def handle_navigation(self, query, data: str):
        """Обрабатывает навигацию по товарам"""
        current_index = int(data.split("_")[1])
        new_index = current_index - 1 if data.startswith("prev_") else current_index + 1
        
        # Получаем товары из сообщения
        products = self.get_products_from_message(query.message)
        
        if products and 0 <= new_index < len(products):
            await self.edit_product_message(query, products, new_index)
    
    async def edit_product_message(self, query, products: List[Dict], index: int):
        """Редактирует сообщение с информацией о товаре"""
        product = products[index]
        text = self.format_product_info(product, index, len(products))
        reply_markup = self.create_product_keyboard(index, len(products), product['product_url'])
        
        try:
            if query.message.photo:
                await query.message.edit_media(
                    InputMediaPhoto(
                        media=product.get('image_url', ''),
                        caption=text,
                        parse_mode="HTML"
                    ),
                    reply_markup=reply_markup
                )
            else:
                await query.message.edit_text(
                    text,
                    parse_mode="HTML",
                    reply_markup=reply_markup,
                    disable_web_page_preview=True
                )
        except Exception as e:
            logger.error(f"Ошибка при редактировании сообщения: {e}")
    
    def get_products_from_message(self, message) -> List[Dict]:
        """Извлекает список товаров из сообщения (упрощенная реализация)"""
        # В реальном боте здесь должна быть логика получения сохраненных товаров
        # Например, можно хранить их в базе данных или context.user_data
        return []
    
    async def show_stats_callback(self, query, context: CallbackContext):
        """Показывает статистику по запросу из callback"""
        if 'last_search' not in context.user_data:
            await query.answer("Сначала выполните поиск товаров.", show_alert=True)
            return
        
        products = context.user_data['last_search']
        stats = self.parser.calculate_price_statistics(products)
        
        text = (
            "📊 <b>Статистика по последнему запросу:</b>\n\n"
            f"🔢 Товаров найдено: {len(products)}\n"
            f"💰 Средняя цена: {stats['average_price']} ₽\n"
            f"📉 Минимальная цена: {stats['min_price']} ₽\n"
            f"📈 Максимальная цена: {stats['max_price']} ₽\n"
            f"🎯 Средняя скидка: {stats['average_discount']}%\n"
            f"🏷️ Товаров со скидкой: {stats['discount_products_count']}"
        )
        
        await query.message.reply_text(text, parse_mode="HTML")
    
    async def show_stats(self, update: Update, context: CallbackContext):
        """Обработчик команды /stats"""
        if 'last_search' not in context.user_data:
            await update.message.reply_text("ℹ️ Сначала выполните поиск товаров.")
            return
        
        products = context.user_data['last_search']
        stats = self.parser.calculate_price_statistics(products)
        
        text = (
            "📊 <b>Статистика по последнему запросу:</b>\n\n"
            f"🔢 Товаров найдено: {len(products)}\n"
            f"💰 Средняя цена: {stats['average_price']} ₽\n"
            f"📉 Минимальная цена: {stats['min_price']} ₽\n"
            f"📈 Максимальная цена: {stats['max_price']} ₽\n"
            f"🎯 Средняя скидка: {stats['average_discount']}%\n"
            f"🏷️ Товаров со скидкой: {stats['discount_products_count']}"
        )
        
        await update.message.reply_text(text, parse_mode="HTML")
    
    async def show_history(self, update: Update, context: CallbackContext):
        """Показывает историю поиска пользователя"""
        user_id = update.effective_user.id
        searches = self.user_searches.get(user_id, [])[-10:]  # Последние 10 запросов
        
        if not searches:
            await update.message.reply_text("ℹ️ Вы еще не выполняли поиск.")
            return
        
        text = "🔍 <b>Последние поисковые запросы:</b>\n\n"
        text += "\n".join(
            f"{i+1}. {s['query']} ({len(s['products'])} товаров)"
            for i, s in enumerate(reversed(searches))
        )
        
        await update.message.reply_text(text, parse_mode="HTML")
    
    def save_user(self, user):
        """Сохраняет информацию о пользователе (упрощенная реализация)"""
        # В реальном боте следует сохранять в базу данных
        pass
    
    def save_search(self, user_id: int, query: str, products: List[Dict]):
        """Сохраняет поисковый запрос в истории"""
        if user_id not in self.user_searches:
            self.user_searches[user_id] = []
        
        self.user_searches[user_id].append({
            "timestamp": datetime.now().timestamp(),
            "query": query,
            "products": products,
        })
    
    async def error_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Логирует ошибки и уведомляет пользователя"""
        logger.error("Ошибка при обработке сообщения:", exc_info=context.error)
        
        if update.effective_message:
            await update.effective_message.reply_text(
                "⚠️ Произошла ошибка. Пожалуйста, попробуйте позже."
            )
    
    def run(self):
        """Запускает бота"""
        self.application.run_polling(allowed_updates=Update.ALL_TYPES)

