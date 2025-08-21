import requests
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Tuple, Optional, Any
from fake_useragent import UserAgent
from bs4 import BeautifulSoup
from io import BytesIO
from django.core.files import File
from urllib.parse import urlparse
from .models import Product, ProductImage
from django.core.cache import cache
import asyncio
import aiohttp
from functools import lru_cache
from PIL import Image
import uuid
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
            'check_urls': 50,  # Максимум URL для проверки
            'download': 16     # Максимум изображений для загрузки
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
    
    def _get_image_urls_from_api(self, product_id: int) -> List[str]:
        """Получение URL изображений через API с проверкой доступности"""
        try:
            response = requests.get(
                f"https://card.wb.ru/cards/detail?nm={product_id}",
                headers={'User-Agent': self.ua.random},
                timeout=5
            )
            if response.status_code != 200:
                return []
                
            data = response.json()
            products = data.get('data', {}).get('products', [])
            if not products:
                return []
                
            # Получаем только большие изображения
            pics = products[0].get('pics', [])
            return [f"https://images.wbstatic.net/big/new/{pic}.jpg" for pic in pics]
            
        except Exception as e:
            logger.warning(f"Ошибка API для товара {product_id}: {str(e)}")
            return []

    @lru_cache(maxsize=1000)
    def _generate_all_image_urls(self, product_id: int) -> List[str]:
        """Генерация только больших URL изображений Wildberries"""
        product_id = int(product_id)
        urls = set()
        vol = product_id // 100000
        part = product_id // 1000

        # Основные серверы (1-40)
        servers = list(range(1, 40))
        
        # Альтернативные домены
        domains = ['wbbasket.ru', 'wb.ru', 'wildberries.ru']
        
        # Только большие форматы изображений
        formats = ['big', 'c516x688']
        
        # Генерация всех возможных комбинаций URL
        for domain in domains:
            for server in servers:
                base_url = f"https://basket-{server:02d}.{domain}/vol{vol}/part{part}/{product_id}/images"
                
                for img_format in formats:
                    for img_num in range(1, 2):  # До 10 изображений на товар
                        urls.update({
                            f"{base_url}/{img_format}/{img_num}.webp",
                            f"{base_url}/{img_format}/{img_num}.jpg",
                        })

        # Добавляем только большие URL из API Wildberries
        api_urls = self._get_image_urls_from_api(product_id)
        if api_urls:
            urls.update(api_urls[:4])

        # Только большие форматы WB
        urls.update({
            f"https://images.wbstatic.net/big/new/{product_id}-1.jpg",
            f"https://images.wbstatic.net/big/new/{product_id}-2.jpg",
            f"https://images.wbstatic.net/big/new/{product_id}-1.webp",
            f"https://images.wbstatic.net/big/new/{product_id}-2.webp",
        })

        # CDN URL только большие
        urls.update({
            f"https://cdn.wbstatic.net/big/new/{product_id}-1.jpg",
            f"https://cdn.wbstatic.net/big/new/{product_id}-2.jpg",
            f"https://cdn.wbstatic.net/c516x688/new/{product_id}-1.jpg",
            f"https://cdn.wbstatic.net/c516x688/new/{product_id}-2.jpg"
        })

        return list(urls)[:350]

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
    
    def _get_size_from_url(self, url: str) -> str:
        """Определение размера изображения из URL"""
        if 'c516x688' in url:
            return '516x688'
        elif 'big' in url or 'original' in url:
            return 'big'
        return 'unknown'
    
    def get_all_image_urls(self, product_id: int) -> List[Dict[str, str]]:
        """Синхронная обертка для получения всех URL изображений"""
        return asyncio.run(self._get_all_valid_image_urls_async(product_id))
    
    
    # def _download_image(self, url: str) -> Optional[Tuple[BytesIO, str]]:
    #     """Загрузка изображения с возвратом данных и типа"""
    #     try:
    #         headers = {
    #             'User-Agent': self.ua.random,
    #             'Accept': 'image/webp,image/apng,image/*,*/*;q=0.8',
    #             'Referer': 'https://www.wildberries.ru/',
    #         }
            
    #         response = requests.get(url, headers=headers, 
    #                             stream=True, timeout=(3.05, 10))
    #         response.raise_for_status()
            
    #         content_type = response.headers.get('Content-Type', '')
    #         if not content_type.startswith('image/'):
    #             return None
                
    #         img_data = BytesIO()
    #         for chunk in response.iter_content(chunk_size=8192):
    #             img_data.write(chunk)
                
    #         img_data.seek(0)
            
    #         # Проверка валидности изображения
    #         try:
    #             img = Image.open(img_data)
    #             img.verify()
    #             img_data.seek(0)
    #             return (img_data, content_type.split('/')[-1].split(';')[0])
    #         except:
    #             return None
                
    #     except Exception as e:
    #         logger.debug(f"Ошибка загрузки изображения {url}: {str(e)}")
    #         return None

    @timing_decorator  
    async def download_images_async(self, product_id: int) -> List[Dict[str, Any]]:
        """Асинхронная загрузка всех изображений для товара"""
        start_time = time.time()
        
        image_urls = await self._get_valid_image_urls_async(product_id)
        if not image_urls:
            logger.warning(f"Не найдено рабочих URL для товара {product_id}")
            return []
        
        downloaded_images = []
        
        async with aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(limit=10),
            timeout=aiohttp.ClientTimeout(total=30),
            headers={'User-Agent': self.ua.random}
        ) as session:
            tasks = []
            for img_info in image_urls[:self.image_limits['download']]:
                tasks.append(self._download_image_async(session, img_info))
            
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            for i, result in enumerate(results):
                if result and not isinstance(result, Exception):
                    downloaded_images.append(result)
                    logger.info(f"Успешно загружено изображение {i+1} для {product_id}")
                elif isinstance(result, Exception):
                    logger.warning(f"Ошибка загрузки изображения {i+1} для {product_id}: {result}")
        
        end_time = time.time()
        logger.info(f"Загрузка изображений для {product_id} заняла {end_time - start_time:.2f} сек, загружено: {len(downloaded_images)}")
        
        return downloaded_images
    
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
    def save_all_product_images(self, product: Product) -> int:
        """Сохранение всех изображений товара"""
        downloaded_images = self.download_images_async(product.product_id)
        saved_count = 0
        
        for img in downloaded_images:
            try:
                img_name = f"wb_{product.product_id}_{uuid.uuid4().hex[:6]}.{img['type']}"
                
                product_image = ProductImage(
                    product=product,
                    image_url=img['url'],
                    image_size=img['size'],
                    image_type=img['type']
                )
                product_image.image.save(img_name, File(img['data']), save=True)
                saved_count += 1
                
            except Exception as e:
                logger.error(f"Ошибка сохранения изображения {img['url']}: {str(e)}")
        
        # Обновляем основное изображение товара, если есть загруженные
        if downloaded_images:
            product.image_url = downloaded_images[0]['url']
            product.save()
        
        return saved_count

    async def _process_product_images_async(self, product: Product) -> bool:
        """Асинхронная обработка изображений"""
        try:
            downloaded_images = await self.download_images_async(product.product_id)
            
            if not downloaded_images:
                logger.warning(f"Не найдено изображений для товара {product.product_id}")
                return False
            
            # Сохраняем только первое изображение как основное
            if downloaded_images:
                main_image = downloaded_images[0]
                # Обертываем обновление в sync_to_async
                await sync_to_async(setattr)(product, 'image_url', main_image['url'])
                await sync_to_async(product.save)()
                
                # Остальные изображения можно сохранять в фоне
                asyncio.create_task(self._save_additional_images_async(product, downloaded_images[1:]))
            
            return True
            
        except Exception as e:
            logger.error(f"Ошибка обработки изображений: {str(e)}")
            return False
        
    async def _save_additional_images_async(self, product: Product, images: List[Dict]):
        """Фоновая сохранение дополнительных изображений"""
        for img in images:
            try:
                img_name = f"wb_{product.product_id}_{uuid.uuid4().hex[:6]}.{img['type']}"
                
                # Создаем ProductImage асинхронно
                product_image = ProductImage(
                    product=product,
                    image_url=img['url'],
                    image_size=img['size'],
                    image_type=img['type']
                )
                
                # Обертываем сохранение в sync_to_async
                await sync_to_async(product_image.image.save)(
                    img_name, 
                    File(img['data']), 
                    save=True
                )
                
            except Exception as e:
                logger.error(f"Ошибка сохранения дополнительного изображения: {str(e)}")

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

    async def _save_products_async(self, products_data: List[Dict]) -> int:
        """Асинхронное сохранение товаров"""
        saved_count = 0
        
        for product_data in products_data:
            try:
                # Обертываем ORM вызовы в sync_to_async
                product, created = await sync_to_async(Product.objects.update_or_create)(
                    product_id=product_data['product_id'],
                    defaults={
                        'name': product_data['name'],
                        'price': product_data['price'],
                        'discount_price': product_data['discount_price'],
                        'wildberries_card_price': product_data['wildberries_card_price'],
                        'rating': product_data['rating'],
                        'reviews_count': product_data['reviews_count'],
                        'product_url': product_data['product_url'],
                        'category': product_data['category'],
                        'search_query': product_data['search_query'],
                        'image_url': product_data['image_url'],
                        'has_wb_card_discount': product_data.get('has_wb_card_discount', False),
                        'quantity': product_data.get('quantity', 0),
                        'is_available': product_data.get('is_available', False)
                    }
                )
                
                # Асинхронная обработка изображений
                if await self._process_product_images_async(product):
                    saved_count += 1
                    logger.info(f"Успешно сохранен товар {product.product_id}")
                    
            except Exception as e:
                logger.error(f"Ошибка сохранения товара {product_data.get('product_id')}: {str(e)}")
        
        return saved_count
    
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
    
    def get_performance_stats(self):
        """Возвращает статистику производительности"""
        return {
            'total_parsing_time': self.total_parsing_time,
            'parsing_count': self.parsing_count,
            'average_time': self.total_parsing_time / self.parsing_count if self.parsing_count > 0 else 0
        }

    def parse_products(self, query: str, category: str = "", limit: int = 10) -> int:
        """Алиас для parse_and_save"""
        return self.parse_and_save(query, category, limit)
    
    def parse_and_save(self, query: str, category: str = "", limit: int = 10) -> int:
        """Синхронная обертка для обратной совместимости"""
        return asyncio.run(self.parse_and_save_async(query, category, limit))

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

    def save_product_with_images(self, product_data: Dict) -> bool:
        """Сохранение товара с изображениями"""
        try:
            # Создаем/обновляем товар
            product, created = Product.objects.update_or_create(
                product_id=product_data['product_id'],
                defaults={
                    'name': product_data['name'],
                    'price': product_data['price'],
                    'discount_price': product_data['discount_price'],
                    'wildberries_card_price': product_data.get('wildberries_card_price'),
                    'rating': product_data['rating'],
                    'reviews_count': product_data['reviews_count'],
                    'product_url': product_data['product_url'],
                    'category': product_data['category'],
                    'search_query': product_data['search_query'],
                    'image_url': product_data['image_url'],
                    'brand': product_data.get('brand', ''),
                    'description': product_data.get('description', '')
                }
            )

            # Удаляем старые изображения, если они есть
            product.images.all().delete()

            # Сохраняем новые изображения
            for img_data in product_data.get('images', [])[:10]:  # Сохраняем до 10 изображений
                try:
                    img_name = f"wb_{product.product_id}_{uuid.uuid4().hex[:6]}.{img_data['type']}"
                    
                    # Создаем объект изображения
                    img_file = File(BytesIO(img_data['data']), name=img_name)
                    
                    # Сохраняем изображение
                    ProductImage.objects.create(
                        product=product,
                        image=img_file,
                        image_url=img_data['url'],
                        image_size=img_data.get('size', ''),
                        image_type=img_data.get('type', ''),
                        is_main=(img_data['url'] == product.image_url)
                    )
                except Exception as e:
                    logger.error(f"Ошибка сохранения изображения: {str(e)}")

            return True
        except Exception as e:
            logger.error(f"Ошибка сохранения товара {product_data.get('product_id')}: {str(e)}")
            return False
        
    async def _get_valid_image_urls_async(self, product_id: int) -> List[Dict]:
        """Асинхронная проверка URL изображений с кешированием"""
        logger.info(f"Начинаем поиск URL для товара {product_id}")
        
        cache_key = f"wb_images_{product_id}"
        cached = cache.get(cache_key)
        if cached:
            logger.info(f"Найдены кешированные URL для {product_id}: {len(cached)} шт")
            return cached

        urls = self._generate_all_image_urls(product_id)
        logger.info(f"Сгенерировано URL для {product_id}: {len(urls)} шт")
        
        valid_urls = []
        
        async with aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(limit=20),
            timeout=aiohttp.ClientTimeout(total=15),
            headers={'User-Agent': self.ua.random}
        ) as session:
            # Проверяем все URL параллельно
            tasks = [self._check_and_analyze_image(session, url) for url in urls]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            for result in results:
                if result and not isinstance(result, Exception):
                    valid_urls.append(result)
                elif isinstance(result, Exception):
                    logger.debug(f"Ошибка проверки URL: {result}")

        logger.info(f"Найдено рабочих URL для {product_id}: {len(valid_urls)} шт")
        
        # Сортируем изображения по приоритету
        valid_urls.sort(key=lambda x: (
            0 if 'big' in x['url'] else 
            1 if 'c516x688' in x['url'] else 
            2
        ))

        cache.set(cache_key, valid_urls, timeout=3600)
        return valid_urls[:8]

    async def _check_and_analyze_image(self, session, url: str) -> Optional[Dict]:
        """Проверка и анализ изображения с повторными попытками"""
        max_retries = 2
        for attempt in range(max_retries):
            try:
                async with session.head(url, allow_redirects=True, timeout=aiohttp.ClientTimeout(total=5)) as response:
                    if response.status == 200:
                        content_type = response.headers.get('Content-Type', '')
                        if content_type and content_type.startswith('image/'):
                            img_type = content_type.split('/')[-1].split(';')[0]
                            return {
                                'url': str(response.url),
                                'type': img_type,
                                'size': self._get_size_from_url(str(response.url))
                            }
                    elif response.status == 404:
                        return None
                        
            except asyncio.TimeoutError:
                logger.debug(f"Таймаут проверки URL {url} (попытка {attempt + 1})")
                if attempt == max_retries - 1:
                    return None
                await asyncio.sleep(0.5)
                
            except Exception as e:
                logger.debug(f"Ошибка проверки URL {url}: {str(e)}")
                if attempt == max_retries - 1:
                    return None
                await asyncio.sleep(0.5)
        
        return None
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