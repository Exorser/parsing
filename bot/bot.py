import sys
import os
from pathlib import Path
import django

# 1. Добавляем корень проекта в PYTHONPATH
BASE_DIR = Path(__file__).resolve().parent.parent.parent  # Wildberries_parser/
sys.path.append(str(BASE_DIR))

# 2. Правильная настройка Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend.project.settings')
try:
    django.setup()
except Exception as e:
    print(f"Django setup error: {e}")
    # Если Django не требуется, можно продолжить без него
    class Product: pass
    class ProductImage: pass
else:
    from backend.app.models import Product, ProductImage

# 3. Импорт парсера
try:
    from backend.app.parser import WildberriesParser
except ImportError:
    from parser import WildberriesParser
import logging
from aiogram import Bot, Dispatcher, types, F
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder
from io import BytesIO
from PIL import Image


BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(BASE_DIR))

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Инициализация бота и диспетчера
bot = Bot(token="8124289862:AAGPVxgf5gyphHU1SUwVfgozwbEL9a1NO24")
storage = MemoryStorage()
dp = Dispatcher(bot=bot, storage=storage)

# Инициализация парсера
parser = WildberriesParser()

# Состояния для FSM
class SearchStates(StatesGroup):
    waiting_for_query = State()
    waiting_for_category = State()
    waiting_for_limit = State()

# Команда /start
@dp.message_handler(commands=['start'])
async def cmd_start(message: types.Message):
    welcome_text = (
        "👋 Привет! Я бот для поиска товаров на Wildberries.\n\n"
        "🔍 Отправьте мне название товара, который хотите найти, "
        "или используйте команду /search для начала поиска.\n\n"
        "ℹ️ Вы также можете использовать команды:\n"
        "/help - показать справку\n"
        "/stats - показать статистику\n"
        "/history - показать историю поиска"
    )
    await message.answer(welcome_text)

# Команда /help
@dp.message_handler(commands=['help'])
async def cmd_help(message: types.Message):
    help_text = (
        "📚 Справка по использованию бота:\n\n"
        "🔍 Поиск товаров:\n"
        "1. Используйте команду /search\n"
        "2. Введите поисковый запрос (например, 'кроссовки')\n"
        "3. Укажите категорию (необязательно)\n"
        "4. Укажите количество товаров (по умолчанию 5)\n\n"
        "⚡ Быстрый поиск: просто отправьте боту название товара\n\n"
        "📊 Статистика: /stats - показывает статистику по последнему поиску\n\n"
        "🔄 История: /history - показывает ваши последние поисковые запросы"
    )
    await message.answer(help_text)

# Команда /search - запускает процесс поиска
@dp.message(F.text == '/search')
async def cmd_search(message: types.Message, state: FSMContext):
    await state.set_state(SearchStates.waiting_for_query)
    await message.answer("🔍 Введите поисковый запрос (например, 'кроссовки'):")

# Обработчик текстовых сообщений (быстрый поиск)
@dp.message_handler(state="*")
async def quick_search(message: types.Message):
    query = message.text.strip()
    if len(query) < 2:
        await message.answer("❌ Слишком короткий запрос. Попробуйте еще раз.")
        return
    
    await SearchStates.waiting_for_category.set()
    await message.answer(f"🔍 Вы ищете: <b>{query}</b>\n\nТеперь укажите категорию (необязательно):", parse_mode="HTML")
    
    # Сохраняем запрос в контексте
    async with dp.current_state(chat=message.chat.id).proxy() as data:
        data['query'] = query

# Обработчик категории
@dp.message_handler(state=SearchStates.waiting_for_category)
async def process_category(message: types.Message, state: FSMContext):
    category = message.text.strip()
    
    async with state.proxy() as data:
        data['category'] = category if category else "Без категории"
    
    await SearchStates.next()
    await message.answer("📊 Укажите количество товаров (от 1 до 20):")

# Обработчик количества товаров
@dp.message_handler(state=SearchStates.waiting_for_limit)
async def process_limit(message: types.Message, state: FSMContext):
    try:
        limit = int(message.text.strip())
        if limit < 1 or limit > 20:
            raise ValueError
    except ValueError:
        await message.answer("❌ Некорректное число. Введите число от 1 до 20.")
        return
    
    async with state.proxy() as data:
        query = data['query']
        category = data['category']
    
    # Уведомление о начале поиска
    msg = await message.answer(f"🔍 Ищу товары по запросу: <b>{query}</b>...", parse_mode="HTML")
    
    try:
        # Парсим товары асинхронно
        products = await parser.parse_products_async(query, category, limit)
        
        if not products:
            await message.answer("❌ По вашему запросу ничего не найдено.")
            await state.finish()
            return
        
        # Сохраняем результаты поиска
        async with state.proxy() as data:
            data['last_results'] = products
        
        # Отправляем первый товар с клавиатурой навигации
        await send_product(message.chat.id, products[0], 0, len(products))
        
    except Exception as e:
        logger.error(f"Ошибка при поиске товаров: {e}", exc_info=True)
        await message.answer("⚠️ Произошла ошибка при поиске товаров. Попробуйте позже.")
    finally:
        # Не завершаем состояние, чтобы можно было использовать навигацию
        pass

# Функция для отправки информации о товаре
async def send_product(chat_id, product, current_index, total_count):
    # Формируем текст сообщения
    text = (
        f"🛍 <b>{product['name']}</b>\n\n"
        f"💰 Цена: <b>{product['price']:.2f} ₽</b>\n"
    )
    
    if product.get('discount_price'):
        text += f"💸 Цена со скидкой: <b>{product['discount_price']:.2f} ₽</b>\n"
        discount_percent = ((product['price'] - product['discount_price']) / product['price'] * 100)
        text += f"🎁 Скидка: <b>{discount_percent:.0f}%</b>\n"
    
    if product.get('wildberries_card_price'):
        text += f"💳 С картой WB: <b>{product['wildberries_card_price']:.2f} ₽</b>\n"
    
    text += (
        f"\n⭐ Рейтинг: <b>{product['rating']:.1f}</b>\n"
        f"📝 Отзывов: <b>{product['reviews_count']}</b>\n"
        f"🏷 Категория: <b>{product['category']}</b>\n\n"
        f"Товар {current_index + 1} из {total_count}"
    )
    
    # Создаем клавиатуру навигации
    kb = InlineKeyboardMarkup(row_width=3)
    
    # Кнопки навигации
    buttons = []
    if current_index > 0:
        buttons.append(InlineKeyboardButton("⬅️ Назад", callback_data=f"prev_{current_index - 1}"))
    
    buttons.append(InlineKeyboardButton("🔗 Открыть на WB", url=product['product_url']))
    
    if current_index < total_count - 1:
        buttons.append(InlineKeyboardButton("Вперёд ➡️", callback_data=f"next_{current_index + 1}"))
    
    kb.add(*buttons)
    
    # Кнопки дополнительных действий
    kb.row(
        InlineKeyboardButton("📊 Статистика", callback_data="stats"),
        InlineKeyboardButton("💾 Сохранить", callback_data=f"save_{current_index}")
    )
    
    # Отправляем изображение товара, если есть
    if product.get('image_url'):
        try:
            # Загружаем изображение
            img_data = parser._download_image(product['image_url'])
            if img_data:
                img_bytes, img_type = img_data
                
                # Отправляем фото с описанием
                await bot.send_photo(
                    chat_id=chat_id,
                    photo=img_bytes,
                    caption=text,
                    parse_mode="HTML",
                    reply_markup=kb
                )
                return
        except Exception as e:
            logger.error(f"Ошибка при загрузке изображения: {e}")
    
    # Если изображение не загрузилось, отправляем только текст
    await bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode="HTML",
        reply_markup=kb
    )

# Обработчик callback-запросов (навигация по товарам)
@dp.callback_query_handler(lambda c: c.data.startswith(('prev_', 'next_')))
async def process_callback_navigation(callback_query: types.CallbackQuery, state: FSMContext):
    await bot.answer_callback_query(callback_query.id)
    
    action, index = callback_query.data.split('_')
    index = int(index)
    
    async with state.proxy() as data:
        products = data['last_results']
    
    if 0 <= index < len(products):
        await bot.delete_message(callback_query.message.chat.id, callback_query.message.message_id)
        await send_product(callback_query.message.chat.id, products[index], index, len(products))

# Обработчик кнопки "Статистика"
@dp.callback_query_handler(lambda c: c.data == 'stats')
async def process_callback_stats(callback_query: types.CallbackQuery, state: FSMContext):
    await bot.answer_callback_query(callback_query.id)
    
    async with state.proxy() as data:
        products = data.get('last_results', [])
    
    if not products:
        await bot.send_message(callback_query.message.chat.id, "❌ Нет данных для статистики.")
        return
    
    # Рассчитываем статистику
    prices = [p['price'] for p in products]
    discount_prices = [p['discount_price'] for p in products if p.get('discount_price')]
    ratings = [p['rating'] for p in products]
    
    avg_price = sum(prices) / len(prices) if prices else 0
    min_price = min(prices) if prices else 0
    max_price = max(prices) if prices else 0
    
    avg_rating = sum(ratings) / len(ratings) if ratings else 0
    
    if discount_prices:
        avg_discount = sum((p['price'] - p['discount_price']) / p['price'] * 100 
                          for p in products if p.get('discount_price')) / len(discount_prices)
    else:
        avg_discount = 0
    
    # Формируем текст статистики
    text = (
        "📊 <b>Статистика по результатам поиска:</b>\n\n"
        f"📦 Товаров найдено: <b>{len(products)}</b>\n\n"
        f"💰 Средняя цена: <b>{avg_price:.2f} ₽</b>\n"
        f"📉 Минимальная цена: <b>{min_price:.2f} ₽</b>\n"
        f"📈 Максимальная цена: <b>{max_price:.2f} ₽</b>\n\n"
    )
    
    if discount_prices:
        text += (
            f"🎁 Товаров со скидкой: <b>{len(discount_prices)}</b>\n"
            f"💸 Средняя скидка: <b>{avg_discount:.1f}%</b>\n\n"
        )
    
    text += f"⭐ Средний рейтинг: <b>{avg_rating:.1f}/5</b>"
    
    await bot.send_message(callback_query.message.chat.id, text, parse_mode="HTML")

# Обработчик кнопки "Сохранить"
@dp.callback_query_handler(lambda c: c.data.startswith('save_'))
async def process_callback_save(callback_query: types.CallbackQuery, state: FSMContext):
    await bot.answer_callback_query(callback_query.id)
    
    _, index = callback_query.data.split('_')
    index = int(index)
    
    async with state.proxy() as data:
        products = data.get('last_results', [])
        query = data.get('query', '')
    
    if 0 <= index < len(products):
        product = products[index]
        
        # Здесь можно добавить логику сохранения товара в базу данных
        # или отправки пользователю в удобном формате
        
        await bot.send_message(
            callback_query.message.chat.id,
            f"✅ Товар <b>{product['name']}</b> сохранен.\n\n"
            f"🔍 Поисковый запрос: <b>{query}</b>",
            parse_mode="HTML"
        )

# Команда /stats - показывает статистику по последнему поиску
@dp.message_handler(commands=['stats'])
async def cmd_stats(message: types.Message, state: FSMContext):
    async with state.proxy() as data:
        products = data.get('last_results', [])
    
    if not products:
        await message.answer("❌ Нет данных для статистики. Сначала выполните поиск.")
        return
    
    # Рассчитываем статистику (аналогично обработчику callback)
    prices = [p['price'] for p in products]
    discount_prices = [p['discount_price'] for p in products if p.get('discount_price')]
    ratings = [p['rating'] for p in products]
    
    avg_price = sum(prices) / len(prices) if prices else 0
    min_price = min(prices) if prices else 0
    max_price = max(prices) if prices else 0
    
    avg_rating = sum(ratings) / len(ratings) if ratings else 0
    
    if discount_prices:
        avg_discount = sum((p['price'] - p['discount_price']) / p['price'] * 100 
                      for p in products if p.get('discount_price')) / len(discount_prices)
    else:
        avg_discount = 0
    
    # Формируем текст статистики
    text = (
        "📊 <b>Статистика по последнему поиску:</b>\n\n"
        f"📦 Товаров найдено: <b>{len(products)}</b>\n\n"
        f"💰 Средняя цена: <b>{avg_price:.2f} ₽</b>\n"
        f"📉 Минимальная цена: <b>{min_price:.2f} ₽</b>\n"
        f"📈 Максимальная цена: <b>{max_price:.2f} ₽</b>\n\n"
    )
    
    if discount_prices:
        text += (
            f"🎁 Товаров со скидкой: <b>{len(discount_prices)}</b>\n"
            f"💸 Средняя скидка: <b>{avg_discount:.1f}%</b>\n\n"
        )
    
    text += f"⭐ Средний рейтинг: <b>{avg_rating:.1f}/5</b>"
    
    await message.answer(text, parse_mode="HTML")

# Команда /history - показывает историю поиска
@dp.message_handler(commands=['history'])
async def cmd_history(message: types.Message, state: FSMContext):
    async with state.proxy() as data:
        query = data.get('query', '')
        category = data.get('category', '')
    
    if not query:
        await message.answer("❌ История поиска пуста.")
        return
    
    text = (
        "🕒 <b>История поиска:</b>\n\n"
        f"🔍 Последний запрос: <b>{query}</b>\n"
        f"🏷 Категория: <b>{category}</b>"
    )
    
    await message.answer(text, parse_mode="HTML")

if __name__ == '__main__':
    dp.run_polling(bot, skip_updates=True)