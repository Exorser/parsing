import requests
import logging
from typing import List, Dict, Tuple, Optional, Any
from fake_useragent import UserAgent
from io import BytesIO
from django.core.files import File
from .models import Product, ProductImage
from django.core.cache import cache
import asyncio
import aiohttp
from functools import lru_cache
from PIL import Image
import time
from functools import wraps
import math
from asgiref.sync import sync_to_async

logger = logging.getLogger(__name__)

# Декоратор для измерения времени
def timing_decorator(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        start_time = time.time()
        result = func(*args, **kwargs)
        end_time = time.time()
        execution_time = end_time - start_time
        logger.info(f"Метод {func.__name__} выполнился за {execution_time:.2f} секунд")
        return result
    return wrapper

class WildberriesParser:
    def __init__(self):
        self.session = requests.Session()
        self.ua = UserAgent()
        self.base_url = "https://www.wildberries.ru"
        self.search_url = "https://search.wb.ru/exactmatch/ru/common/v4/search"
        self.timeout = 5
        self.max_workers = 10
        self.image_limits = {
            'check_urls': 15,  # Максимум URL для проверки
            'download': 1    # Максимум изображений для загрузки
        }
        self.total_parsing_time = 0
        self.parsing_count = 0
        
        
        self.session.headers.update({
            'User-Agent': self.ua.random,
            'Accept': 'image/webp,image/apng,image/*,*/*;q=0.8',
            'Accept-Language': 'ru-RU,ru;q=0.9,en;q=0.8',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Sec-Fetch-Dest': 'empty',
            'Sec-Fetch-Mode': 'cors',
            'Sec-Fetch-Site': 'same-site',
            'Cache-Control': 'no-cache',
            'Pragma': 'no-cache',
            'Referer': 'https://www.wildberries.ru/'
        })
   
    # Поиск товаров

    @timing_decorator
    def search_products(self, query: str, limit: int = 10) -> List[Dict]:
        """Поиск разнообразных товаров (разные цены, рейтинги)"""
        try:
            params = {
                "query": query,
                "resultset": "catalog",
                "limit": limit * 2,  # Берем больше, чтобы выбрать разнообразные
                "sort": "popular",    # Популярные товары
                "dest": -1257786,
                "regions": "80,64,38,4,115,83,33,68,70,69,30,86,75,40,1,66,48,110,31,22,71,114",
                "spp": 30,
                "curr": "rub",
                "lang": "ru",
                "locale": "ru",
                "appType": 1,
                "feedbacksCount": 50  # Минимум 5 отзывов
            }
            
            logger.info(f"Поиск разнообразных товаров: {query}")
            response = self.session.get(
                "https://search.wb.ru/exactmatch/ru/common/v5/search",
                params=params,
                timeout=30
            )
            data = response.json()
            
            products = []
            if 'data' in data and 'products' in data['data']:
                products = data['data']['products']
            elif 'products' in data:
                products = data['products']
            
            if not products:
                return []
            
            # Разделяем товары на группы по рейтингу
            high_rated = [p for p in products if p.get('rating', 0) >= 4.5]
            medium_rated = [p for p in products if 4.0 <= p.get('rating', 0) < 4.5]
            low_rated = [p for p in products if p.get('rating', 0) < 4.0]
            
            # Разделяем товары на группы по цене
            min_price = min(p.get('salePriceU', p.get('priceU', 0)) for p in products)
            max_price = max(p.get('salePriceU', p.get('priceU', 0)) for p in products)
            price_step = (max_price - min_price) / 3
            
            cheap = [p for p in products if p.get('salePriceU', p.get('priceU', 0)) < min_price + price_step]
            medium = [p for p in products if min_price + price_step <= p.get('salePriceU', p.get('priceU', 0)) < min_price + 2*price_step]
            expensive = [p for p in products if p.get('salePriceU', p.get('priceU', 0)) >= min_price + 2*price_step]
            
            # Выбираем товары из разных групп
            result = []
            groups = [
                high_rated, medium_rated, low_rated,
                cheap, medium, expensive
            ]
            
            # Распределяем товары из разных групп
            while len(result) < limit and any(groups):
                for group in groups:
                    if group and len(result) < limit:
                        result.append(group.pop(0))
            
            logger.info(f"Отобрано {len(result)} разнообразных товаров")
            return self._parse_products(result[:limit])
        
        except Exception as e:
            logger.error(f"Ошибка при поиске разнообразных товаров: {e}", exc_info=True)
            return []

    # Генерация и проверка URL

    @lru_cache(maxsize=1000)
    def _generate_smart_image_urls(self, product_id: int) -> List[str]:
        """Ультра-надежная генерация URL - только 100% рабочие шаблоны"""
        product_id = int(product_id)
        urls = []
        
        # 1. ОСНОВНОЙ шаблон Wildberries (99% работают!)
        vol = product_id // 100000
        part = product_id // 1000
        
        # Всего 3 самых надежных сервера
        servers = list(range(1, 40))
        
        for server in servers:
            # Основные форматы в порядке приоритета
            urls.extend([
                f"https://basket-{server:02d}.wbbasket.ru/vol{vol}/part{part}/{product_id}/images/big/1.webp",
                f"https://basket-{server:02d}.wb.ru/vol{vol}/part{part}/{product_id}/images/big/1.webp",
            ])
        
        # 2. Стандартные CDN URL (резервные)
        urls.extend([
            f"https://images.wbstatic.net/big/new/{product_id}-1.jpg"
        ])
        
        # 3. API URL (добавляем последними, так как могут быть медленными)
        api_urls = self._get_image_urls_from_api(product_id)
        if api_urls:
            urls.extend(api_urls[:2])
        
        logger.info(f"Сгенерировано {len(urls)} надежных URL для {product_id}")
        return urls
    
    def _generate_all_image_urls(self, product_id: int) -> List[str]:
        """Умная генерация URL - максимум 20 самых вероятных"""
        return self._generate_smart_image_urls(product_id)[:90] 
    
    async def _get_valid_image_urls_async(self, product_id: int) -> List[Dict]:
        """Максимально упрощенная проверка URL"""
        cache_key = f"wb_images_{product_id}"
        if cached := cache.get(cache_key):
            return cached

        urls = self._generate_all_image_urls(product_id)
        valid_urls = []
        
        async with aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(limit=10),
            timeout=aiohttp.ClientTimeout(total=3),  # Ультра-короткий таймаут
            headers={'User-Agent': self.ua.random}
        ) as session:
            
            tasks = [self._check_and_analyze_image(session, url) for url in urls]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            valid_urls = [r for r in results if r and not isinstance(r, Exception)]

        cache.set(cache_key, valid_urls, timeout=1800)
        return valid_urls

    async def _check_and_analyze_image(self, session, url: str) -> Optional[Dict]:
        """Ультра-быстрая проверка одного URL"""
        try:
            # ОЧЕНЬ короткий таймаут - 2 секунды!
            async with session.head(url, allow_redirects=True, 
                                timeout=aiohttp.ClientTimeout(total=2)) as response:
                
                if response.status == 200:
                    content_type = response.headers.get('Content-Type', '')
                    if content_type and content_type.startswith('image/'):
                        return {
                            'url': str(response.url),
                            'type': content_type.split('/')[-1].split(';')[0],
                            'size': self._get_size_from_url(str(response.url))
                        }
                
                # Если 404 или другой статус - пропускаем быстро
                return None
                        
        except asyncio.TimeoutError:
            return None  # Просто пропускаем таймауты
                    
        except Exception:
            return None  # Пропускаем все ошибки
    # Загрузка изображений 

    @timing_decorator 
    async def _download_image_async(self, session: aiohttp.ClientSession, img_info: Dict) -> Optional[Dict]:
        """Асинхронная загрузка одного изображения с повторными попытками"""
        max_retries = 3
        url = img_info['url']
        
        for attempt in range(max_retries):
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as response:
                    if response.status == 200:
                        content_type = response.headers.get('Content-Type', '')
                        if content_type and content_type.startswith('image/'):
                            img_data = await response.read()
                            
                            # Проверяем, что это валидное изображение
                            try:
                                img = Image.open(BytesIO(img_data))
                                img.verify()
                                return {
                                    'url': str(response.url),
                                    'type': content_type.split('/')[-1].split(';')[0],
                                    'size': img_info['size'],
                                    'data': BytesIO(img_data)
                                }
                            except Exception:
                                logger.warning(f"Невалидное изображение: {url}")
                                return None
                    
                    elif response.status == 404:
                        logger.debug(f"Изображение не найдено: {url}")
                        return None
                        
            except asyncio.TimeoutError:
                logger.debug(f"Таймаут загрузки {url} (попытка {attempt + 1})")
                if attempt < max_retries - 1:
                    await asyncio.sleep(1)
                else:
                    return None
                    
            except Exception as e:
                logger.debug(f"Ошибка загрузки {url}: {str(e)}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(1)
                else:
                    return None
        
        return None
    
    @timing_decorator   
    async def download_main_image_async(self, product_id: int) -> Optional[Dict[str, Any]]:
        """Загрузка главного изображения с многоуровневым fallback"""
        try:
            # 1. Быстрая проверка кеша
            cache_key = f"wb_image_{product_id}"
            cached_image = cache.get(cache_key)
            if cached_image:
                return cached_image
            
            # 2. Получаем URL
            image_urls = await self._get_valid_image_urls_async(product_id)
            if not image_urls:
                # 3. Fallback: пробуем сгенерировать URL напрямую
                direct_url = self._generate_direct_image_url(product_id)
                if direct_url:
                    image_urls = [{'url': direct_url, 'type': 'jpg', 'size': 'big'}]
                else:
                    return None
            
            # 4. Берем первый рабочий URL
            image_info = image_urls[0]
            
            # 5. Быстрая загрузка
            async with aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(limit=5),
                timeout=aiohttp.ClientTimeout(total=8),
                headers={'User-Agent': self.ua.random}
            ) as session:
                
                result = await self._download_image_async(session, image_info)
                
                if result:
                    # Кешируем результат
                    cache.set(cache_key, result, timeout=3600)
                    return result
                    
        except Exception as e:
            logger.error(f"Критическая ошибка загрузки изображения {product_id}: {str(e)}")
        
        return None
    
    # Сохранение данных

    @timing_decorator
    async def parse_and_save_async(self, query: str, category: str = "", limit: int = 10) -> int:
        """Асинхронный парсинг и сохранение"""
        start_time = time.time()
        
        products_data = self.search_products(query, limit)
        if not products_data:
            return 0

        category = category or "Без категории"
        for product in products_data:
            product['search_query'] = query
            product['category'] = category

        # Параллельная обработка товаров
        saved_count = await self._save_products_async(products_data)
        
        end_time = time.time()
        total_time = end_time - start_time
        
        logger.info(
            f"Парсинг завершен! Время: {total_time:.2f} сек, "
            f"Сохранено: {saved_count}/{len(products_data)}, "
            f"Среднее время на товар: {total_time/len(products_data):.2f} сек"
        )
        return saved_count
    
    async def _save_products_async(self, products_data: List[Dict]) -> int:
        """Параллельное сохранение товаров"""
        saved_count = 0
        
        # Обрабатываем товары ПАРАЛЛЕЛЬНО!
        tasks = []
        for product_data in products_data:
            task = self._process_single_product_async(product_data)
            tasks.append(task)
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for result in results:
            if result and not isinstance(result, Exception):
                saved_count += 1
                    
        return saved_count
    
    async def _process_single_product_async(self, product_data: Dict) -> bool:
        """Обработка одного товара"""
        try:
            product, created = await sync_to_async(Product.objects.update_or_create)(
                product_id=product_data['product_id'],
                defaults={
                    'name': product_data['name'],
                    'price': product_data['price'],
                    'discount_price': product_data['discount_price'],
                    'rating': product_data['rating'],
                    'reviews_count': product_data['reviews_count'],
                    'product_url': product_data['product_url'],
                    'category': product_data['category'],
                    'search_query': product_data['search_query'],
                    'image_url': product_data['image_url'],  # URL из поиска
                    'has_wb_card_discount': product_data.get('has_wb_card_discount', False),
                }
            )
            
            # Параллельная загрузка изображения
            if await self._process_product_images_async(product):
                return True
                
        except Exception as e:
            logger.error(f"Ошибка обработки товара: {str(e)}")
        
        return False
    
    async def _process_product_images_async(self, product: Product) -> bool:
        """Упрощенная обработка изображений - только главное"""
        try:
            main_image = await self.download_main_image_async(product.product_id)
            
            if not main_image:
                logger.warning(f"Не найдено изображение для товара {product.product_id}")
                return False
            
            # Сохраняем только главное изображение
            await sync_to_async(setattr)(product, 'image_url', main_image['url'])
            await sync_to_async(product.save)()
            
            # Сохраняем изображение в базу если нужно
            await self._save_main_image_async(product, main_image)
                
            return True
            
        except Exception as e:
            logger.error(f"Ошибка обработки изображений: {str(e)}")
            return False
    
    # Вспомогательные методы

    def _parse_products(self, products_data: List[Dict]) -> List[Dict]:
        """Парсинг данных товаров с учетом всех изображений"""
        parsed_products = []
        
        for product in products_data:
            try:
                product_id = product.get('id')
                if not product_id:
                    continue
                    
                rating = product.get('rating', 0)
                if isinstance(rating, dict):
                    rating = rating.get('rate', 0)
                
                reviews = product.get('feedbacks', 0)
                if isinstance(reviews, dict):
                    reviews = reviews.get('count', 0)
                
                # Используем новый метод для генерации всех URL
                image_urls = self._generate_all_image_urls(int(product_id))
                first_image = image_urls[0] if image_urls else ""

                quantity_info = self._extract_quantity_info(product)
                price_info = self._extract_price_info(product)
                
                parsed_product = {
                    'product_id': str(product_id),
                    'name': product.get('name', ''),
                    **price_info,
                    **quantity_info, 
                    'rating': float(rating) if rating else 0.0,
                    'reviews_count': int(reviews) if reviews else 0,
                    'product_url': f"{self.base_url}/catalog/{product_id}/detail.aspx",
                    'image_url': first_image,
                    'image_urls': image_urls,
                    'category': '',
                    'search_query': ''
                }
                
                parsed_products.append(parsed_product)
                
            except Exception as e:
                logger.error(f"Ошибка парсинга товара {product.get('id', 'unknown')}: {str(e)}")
        
        return parsed_products
    
    def _extract_quantity_info(self, product: Dict) -> Dict[str, Any]:
        """Извлекает информацию о наличии товара"""
        quantity = 0
        is_available = False
        
        # Проверяем наличие в разных структурах данных Wildberries
        if 'sizes' in product and product['sizes']:
            for size in product['sizes']:
                if 'stocks' in size and size['stocks']:
                    for stock in size['stocks']:
                        qty = stock.get('qty', 0)
                        quantity += qty
                        if qty > 0:
                            is_available = True
        
        # Альтернативные пути к данным о количестве
        if quantity == 0:
            quantity = product.get('totalQuantity', 0)
            if quantity > 0:
                is_available = True
        
        if quantity == 0:
            quantity = product.get('quantity', 0)
            if quantity > 0:
                is_available = True
        
        # Проверяем extended данные
        if 'extended' in product:
            ext_quantity = product['extended'].get('basicSale', 0)
            if ext_quantity > 0:
                quantity = ext_quantity
                is_available = True
        
        return {
            'quantity': quantity,
            'is_available': is_available
        }

    def _extract_price_info(self, product: Dict) -> Dict[str, Optional[float]]:
        """Извлекает информацию о ценах товара с округлением в меньшую сторону"""
        price = discount_price = wildberries_card_price = None
        has_wb_card_discount = False
        
        if 'sizes' in product and product['sizes']:
            for size in product['sizes']:
                if 'price' in size:
                    price_data = size['price']
                    basic = price_data.get('basic', 0) / 100
                    product_price = price_data.get('product', 0) / 100
                    
                    if product_price > 0 and product_price < basic:
                        price = basic
                        discount_price = product_price
                        wildberries_card_price = math.floor(product_price * 0.9 * 100) / 100  # Округление вниз
                        has_wb_card_discount = True
                        break
                    else:
                        price = basic if basic > 0 else product_price
                        wildberries_card_price = math.floor(price * 0.9 * 100) / 100  # Округление вниз
        
        if price is None:
            original = product.get('priceU', 0) / 100
            sale = product.get('salePriceU', 0) / 100
            
            if sale > 0 and sale < original:
                price = original
                discount_price = sale
                wildberries_card_price = math.floor(sale * 0.9 * 100) / 100  # Округление вниз
                has_wb_card_discount = True
            else:
                price = original if original > 0 else sale
                wildberries_card_price = math.floor(price * 0.9 * 100) / 100  # Округление вниз
        
        if 'extended' in product and 'basicPriceU' in product['extended']:
            basic_ext = product['extended']['basicPriceU'] / 100
            if price is None or (basic_ext > 0 and basic_ext < price):
                price = basic_ext
                wildberries_card_price = math.floor(price * 0.9 * 100) / 100  # Округление вниз
        
        if 'clientSale' in product and discount_price:
            client_sale = product['clientSale']
            if client_sale > 0:
                discount_price = math.floor(discount_price * (1 - client_sale / 100) * 100) / 100  # Округление вниз
                wildberries_card_price = math.floor(discount_price * 0.9 * 100) / 100  # Округление вниз
        
        return {
            'price': price if price else 0.0,
            'discount_price': discount_price if discount_price and discount_price < price else None,
            'wildberries_card_price': wildberries_card_price if has_wb_card_discount else None,
            'has_wb_card_discount': has_wb_card_discount
        }

    def _get_image_urls_from_api(self, product_id: int) -> List[str]:
        """Супер-быстрое получение URL через API"""
        try:
            # Кешируем запросы к API
            cache_key = f"wb_api_{product_id}"
            cached = cache.get(cache_key)
            if cached:
                return cached
                
            # Быстрый запрос с коротким таймаутом
            response = requests.get(
                f"https://card.wb.ru/cards/detail?nm={product_id}",
                headers={'User-Agent': self.ua.random},
                timeout=2  # Всего 2 секунды!
            )
            
            if response.status_code == 200:
                data = response.json()
                products = data.get('data', {}).get('products', [])
                if products and 'pics' in products[0]:
                    pics = products[0]['pics']
                    if pics:
                        # Только первые 2 изображения
                        result = [f"https://images.wbstatic.net/big/new/{pic}.jpg" for pic in pics[:2]]
                        cache.set(cache_key, result, timeout=300)  # Кешируем на 5 минут
                        return result
                        
        except Exception as e:
            logger.debug(f"Быстрый API запрос не удался: {str(e)}")
        
        return []
    
    def _get_size_from_url(self, url: str) -> str:
        """Определение размера изображения из URL"""
        if 'c516x688' in url:
            return '516x688'
        elif 'big' in url or 'original' in url:
            return 'big'
        return 'unknown'
    
    # Синхронные обертки
    def parse_products(self, query: str, category: str = "", limit: int = 10) -> int:
        """Алиас для parse_and_save"""
        return self.parse_and_save(query, category, limit)
    
    def parse_and_save(self, query: str, category: str = "", limit: int = 10) -> int:
        """Синхронная обертка для обратной совместимости"""
        return asyncio.run(self.parse_and_save_async(query, category, limit))
    
    # Остальные

    def get_product_data(self, product_id: int) -> Optional[Dict]:
        """Получение данных конкретного товара по ID"""
        try:
            response = requests.get(
                f"https://card.wb.ru/cards/detail?nm={product_id}",
                headers={'User-Agent': self.ua.random},
                timeout=5
            )
            if response.status_code != 200:
                return None
                
            data = response.json()
            products = data.get('data', {}).get('products', [])
            if not products:
                return None
                
            product = products[0]
            
            # Преобразуем в тот же формат, что и search_products
            return {
                'product_id': str(product.get('id')),
                'name': product.get('name', ''),
                'price': product.get('priceU', 0) / 100,
                'discount_price': product.get('discount_price', 0) / 100,
                'rating': product.get('rating', 0),
                'reviews_count': product.get('reviews_count', 0),
                'quantity': product.get('quantity', 0),
                # Добавьте другие необходимые поля
            }
            
        except Exception as e:
            logger.error(f"Ошибка получения данных товара {product_id}: {str(e)}")
            return None
    
    async def _check_url_available(self, session: aiohttp.ClientSession, url: str) -> Optional[Tuple[str, str]]:
        """Проверка доступности URL с возвратом типа контента"""
        try:
            async with session.head(url, allow_redirects=True, 
                                timeout=aiohttp.ClientTimeout(total=3)) as response:
                if response.status == 200:
                    content_type = response.headers.get('Content-Type', '')
                    if content_type.startswith('image/'):
                        return (str(response.url), content_type)
        except Exception as e:
            logger.debug(f"Ошибка проверки URL {url}: {str(e)}")
        return None

    async def _get_all_valid_image_urls_async(self, product_id: int) -> List[Dict[str, str]]:
        """Поиск всех рабочих URL изображений с информацией о типе"""
        urls = self._generate_all_image_urls(product_id)
        valid_urls = []
        
        async with aiohttp.ClientSession(headers={
            'User-Agent': self.ua.random,
            'Referer': 'https://www.wildberries.ru/'
        }) as session:
            # Разбиваем URL на группы по 50 для проверки
            for i in range(0, len(urls), 50):
                batch = urls[i:i+50]
                tasks = [self._check_url_available(session, url) for url in batch]
                results = await asyncio.gather(*tasks)
                
                for result in results:
                    if result:
                        url, content_type = result
                        # Пропускаем видео и маленькие изображения
                        if 'video' not in content_type.lower():
                            valid_urls.append({
                                'url': url,
                                'type': content_type.split('/')[-1].split(';')[0],
                                'size': self._get_size_from_url(url)
                            })
                
                # Небольшая задержка между группами
                await asyncio.sleep(0.1)
        
        # Сортируем по размеру (big первыми)
        valid_urls.sort(key=lambda x: (
            0 if 'big' in x['url'] or 'original' in x['url'] else 
            1 if 'c516x688' in x['url'] else 
            2
        ))
        
        return valid_urls 
    
    def get_all_image_urls(self, product_id: int) -> List[Dict[str, str]]:
        """Синхронная обертка для получения всех URL изображений"""
        return asyncio.run(self._get_all_valid_image_urls_async(product_id))
      
    def _generate_direct_image_url(self, product_id: int) -> Optional[str]:
        """Генерация прямого URL в обход проверок"""
        try:
            vol = product_id // 100000
            part = product_id // 1000
            
            # Самый популярный и надежный шаблон
            return f"https://basket-01.wbbasket.ru/vol{vol}/part{part}/{product_id}/images/big/1.webp"
            
        except:
            return None
     
    async def _save_main_image_async(self, product: Product, image: Dict):
        """Сохранение главного изображения"""
        try:
            img_name = f"wb_{product.product_id}_main.{image['type']}"
            
            product_image = ProductImage(
                product=product,
                image_url=image['url'],
                image_size=image['size'],
                image_type=image['type'],
                is_main=True
            )
            
            # Обертываем сохранение в sync_to_async
            await sync_to_async(product_image.image.save)(
                img_name, 
                File(image['data']), 
                save=True
            )
            
        except Exception as e:
            logger.error(f"Ошибка сохранения главного изображения: {str(e)}")

    def calculate_price_statistics(self, products: List[Product]) -> Dict:
        """Расчет статистики по ценам для инфографики"""
        prices = [p.price for p in products if p.price]
        discount_prices = [p.discount_price for p in products if p.discount_price]
        
        return {
            'average_price': round(sum(prices) / len(prices), 2) if prices else 0,
            'min_price': min(prices) if prices else 0,
            'max_price': max(prices) if prices else 0,
            'average_discount': round(
                sum((p.price - p.discount_price) / p.price * 100 
                for p in products if p.discount_price) / 
                len(discount_prices), 1) if discount_prices else 0,
            'discount_products_count': len(discount_prices)
        }

    def calculate_rating_distribution(self, products: List[Product]) -> Dict:
        """Распределение товаров по рейтингу для инфографики"""
        distribution = {
            '5': 0,
            '4-5': 0,
            '3-4': 0,
            '2-3': 0,
            '1-2': 0
        }
        
        for p in products:
            if not p.rating:
                continue
                
            if p.rating == 5:
                distribution['5'] += 1
            elif 4 <= p.rating < 5:
                distribution['4-5'] += 1
            elif 3 <= p.rating < 4:
                distribution['3-4'] += 1
            elif 2 <= p.rating < 3:
                distribution['2-3'] += 1
            else:
                distribution['1-2'] += 1
        
        return distribution
    
    def get_performance_stats(self):
        """Возвращает статистику производительности"""
        return {
            'total_parsing_time': self.total_parsing_time,
            'parsing_count': self.parsing_count,
            'average_time': self.total_parsing_time / self.parsing_count if self.parsing_count > 0 else 0
        }

    async def _fetch_product_data(self, product_id: int) -> Optional[Dict]:
        """Получение полных данных о товаре через API"""
        try:
            async with aiohttp.ClientSession() as session:
                url = f"https://card.wb.ru/cards/detail?nm={product_id}"
                async with session.get(url, headers={'User-Agent': self.ua.random}) as response:
                    if response.status == 200:
                        data = await response.json()
                        return data.get('data', {}).get('products', [{}])[0]
        except Exception as e:
            logger.error(f"Ошибка получения данных товара {product_id}: {str(e)}")
        return None
    
    async def _fetch_product_availability(self, product_id: int) -> Dict[str, Any]:
        """Получение информации о наличии товара через API"""
        try:
            async with aiohttp.ClientSession() as session:
                url = f"https://card.wb.ru/cards/detail?nm={product_id}"
                async with session.get(url, headers={'User-Agent': self.ua.random}) as response:
                    if response.status == 200:
                        data = await response.json()
                        products = data.get('data', {}).get('products', [])
                        if products:
                            return self._extract_quantity_info(products[0])
        except Exception as e:
            logger.error(f"Ошибка получения наличия товара {product_id}: {str(e)}")
        return {'quantity': 0, 'is_available': False}

    def get_product_availability(self, product_id: int) -> Dict[str, Any]:
        """Синхронная обертка для получения информации о наличии"""
        return asyncio.run(self._fetch_product_availability(product_id))

    def update_products_availability(self, products: List[Product]) -> int:
        """Обновление информации о наличии для списка товаров"""
        updated_count = 0
        
        for product in products:
            try:
                availability = self.get_product_availability(int(product.product_id))
                product.quantity = availability['quantity']
                product.is_available = availability['is_available']
                product.save()
                updated_count += 1
                logger.info(f"Обновлено наличие для товара {product.product_id}: {availability}")
            except Exception as e:
                logger.error(f"Ошибка обновления наличия для товара {product.product_id}: {str(e)}")
        
        return updated_count

    async def _process_single_product(self, product_data: Dict) -> Dict:
        """Асинхронная обработка одного товара"""
        product_id = product_data.get('id')
        if not product_id:
            return {}

        # Получаем дополнительные данные
        full_data = await self._fetch_product_data(product_id)
        
        # Формируем данные товара
        product = {
            'product_id': str(product_id),
            'name': product_data.get('name', ''),
            **self._extract_price_info(product_data),
            'rating': self._get_rating(product_data),
            'reviews_count': self._get_reviews_count(product_data),
            'product_url': f"{self.base_url}/catalog/{product_id}/detail.aspx",
            'image_url': '',
            'images': [],
            'category': '',
            'search_query': '',
            'brand': full_data.get('brand', '') if full_data else '',
            'description': full_data.get('description', '') if full_data else ''
        }

        # Получаем изображения
        image_urls = await self._get_valid_image_urls_async(product_id)
        if image_urls:
            product['image_url'] = image_urls[0]['url']
            product['images'] = image_urls[:self.image_limits['download']]

        return product
        
    def _get_rating(self, product_data: Dict) -> float:
        """Извлечение рейтинга из данных товара"""
        rating = product_data.get('rating', 0)
        if isinstance(rating, dict):
            return rating.get('rate', 0)
        return float(rating) if rating else 0.0

    def _get_reviews_count(self, product_data: Dict) -> int:
        """Извлечение количества отзывов"""
        reviews = product_data.get('feedbacks', 0)
        if isinstance(reviews, dict):
            return reviews.get('count', 0)
        return int(reviews) if reviews else 0