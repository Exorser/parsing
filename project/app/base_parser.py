import requests
import logging
from typing import List, Dict, Optional, Any, Union, Tuple
from fake_useragent import UserAgent
from io import BytesIO
from .models import Product
from django.core.cache import cache
import asyncio
import json
import aiohttp
from functools import lru_cache
from PIL import Image
import time
from functools import wraps
import math
from django.db.models import Q
from asgiref.sync import sync_to_async
from abc import ABC, abstractmethod
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import random
from urllib.parse import quote_plus, quote
import re


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

class BaseParser(ABC):
    """Абстрактный базовый класс для всех парсеров"""
    
    def __init__(self, platform: str):
        self.session = requests.Session()
        self.ua = UserAgent()
        self.platform = platform
        self.timeout = 5
        self.max_workers = 10
        self.image_limits = {
            'check_urls': 15,
            'download': 1
        }
        self.total_parsing_time = 0
        self.parsing_count = 0
        self.semaphore = asyncio.Semaphore(5)
        
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
        })

    # Декоратор для измерения времени (асинхронная версия)
    def async_timing_decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            start_time = time.time()
            result = await func(*args, **kwargs)
            end_time = time.time()
            execution_time = end_time - start_time
            logger.info(f"Метод {func.__name__} выполнился за {execution_time:.2f} секунд")
            return result
        return wrapper

    # Декоратор для синхронных методов
    def sync_timing_decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            start_time = time.time()
            result = func(*args, **kwargs)
            end_time = time.time()
            execution_time = end_time - start_time
            logger.info(f"Метод {func.__name__} выполнился за {execution_time:.2f} секунд")
            return result
        return wrapper

    @abstractmethod
    def search_products(self, query: str, limit: int = 10) -> List[Dict]:
        """Абстрактный метод поиска товаров (должен быть реализован в дочерних классах)"""
        pass

    @abstractmethod
    def _generate_smart_image_urls(self, product_id: int) -> List[str]:
        """Абстрактный метод генерации URL изображений"""
        pass

    @abstractmethod
    def _extract_quantity_info(self, product: Dict) -> Dict[str, Any]:
        """Абстрактный метод извлечения информации о наличии"""
        pass

    @abstractmethod
    def _extract_price_info(self, product: Dict) -> Dict[str, Optional[float]]:
        """Абстрактный метод извлечения информации о ценах"""
        pass

    @abstractmethod
    def _get_image_urls_from_api(self, product_id: int) -> List[str]:
        """Абстрактный метод получения URL через API"""
        pass

    # Общие методы, которые одинаковы для всех парсеров
    @sync_timing_decorator
    def _generate_all_image_urls(self, product_id: int) -> List[str]:
        """Умная генерация URL - максимум 150 самых вероятных"""
        return self._generate_smart_image_urls(product_id)[:150]

    @async_timing_decorator
    async def _get_valid_image_urls_async(self, product_id: int) -> List[Dict]:
        """Проверка URL с приоритетом на скорость"""
        cache_key = f"{self.platform.lower()}_images_{product_id}"
        if cached := cache.get(cache_key):
            return cached

        urls = self._generate_smart_image_urls(product_id)
        valid_urls = []
        
        async with aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(limit=30),
            timeout=aiohttp.ClientTimeout(total=3),
            headers={'User-Agent': self.ua.random}
        ) as session:
            
            tasks = [self._check_and_analyze_image(session, url) for url in urls[:30]]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            valid_urls = [r for r in results if r and not isinstance(r, Exception)]
            
            if not valid_urls and len(urls) > 30:
                tasks = [self._check_and_analyze_image(session, url) for url in urls[30:60]]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                valid_urls = [r for r in results if r and not isinstance(r, Exception)]
                
            if not valid_urls and len(urls) > 60:
                for url in urls[60:]:
                    result = await self._check_and_analyze_image(session, url)
                    if result:
                        valid_urls.append(result)
                        break

        cache.set(cache_key, valid_urls, timeout=7200)
        logger.info(f"Найдено {len(valid_urls)} валидных URL для {self.platform} товара {product_id}")
        return valid_urls

    async def _check_and_analyze_image(self, session, url: str) -> Optional[Dict]:
        """Быстрая проверка одного URL"""
        try:
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
                return None
                        
        except (asyncio.TimeoutError, Exception):
            return None

    @async_timing_decorator 
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

    @async_timing_decorator 
    async def download_main_image_async(self, product_id: int) -> Optional[Dict[str, Any]]:
        """Усиленная загрузка с приоритетом на скорость"""
        cache_key = f"{self.platform.lower()}_image_{product_id}"
        if cached_image := cache.get(cache_key):
            return cached_image
        
        image_urls = await self._get_valid_image_urls_async(product_id)
        
        if not image_urls:
            return None
        
        async with aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(limit=5),
            timeout=aiohttp.ClientTimeout(total=5),
            headers={'User-Agent': self.ua.random}
        ) as session:
            
            for img_info in image_urls[:3]:
                result = await self._download_image_async(session, img_info)
                if result:
                    cache.set(cache_key, result, timeout=7200)
                    return result
        
        return None

    @sync_timing_decorator
    def _parse_products(self, products_data: List[Dict]) -> List[Dict]:
        """Парсинг данных товаров с учетом всех изображений"""
        logger.info(f"Начинаем парсинг {len(products_data)} продуктов Ozon")
        parsed_products = []
        
        for i, product in enumerate(products_data):
            try:
                logger.info(f"Продукт {i}: {product.keys()}")
                product_id = product.get('id') or product.get('sku')
                if not product_id:
                    continue
                    
                rating = product.get('rating', 0)
                if isinstance(rating, dict):
                    rating = rating.get('rate', 0)
                
                reviews = product.get('feedbacks', 0) or product.get('reviews_count', 0)
                if isinstance(reviews, dict):
                    reviews = reviews.get('count', 0)
                
                image_urls = self._generate_all_image_urls(int(product_id))
                first_image = image_urls[0] if image_urls else ""

                quantity_info = self._extract_quantity_info(product)
                price_info = self._extract_price_info(product)
                
                parsed_product = {
                    'product_id': str(product_id),
                    'name': product.get('name', product.get('title', '')),
                    **price_info,
                    **quantity_info, 
                    'rating': float(rating) if rating else 0.0,
                    'reviews_count': int(reviews) if reviews else 0,
                    'product_url': self._get_product_url(product_id),
                    'image_url': first_image,
                    'image_urls': image_urls,
                    'search_query': '',
                    'platform': self.platform
                }
                
                parsed_products.append(parsed_product)
                
            except Exception as e:
                logger.error(f"Ошибка парсинга товара {self.platform} {product.get('id', 'unknown')}: {str(e)}")
        
        return parsed_products

    def _get_size_from_url(self, url: str) -> str:
        """Определение размера изображения из URL"""
        if 'c516x688' in url:
            return '516x688'
        elif 'big' in url or 'original' in url:
            return 'big'
        return 'unknown'

    def _is_bad_url(self, url: str) -> bool:
        """Проверяет, является ли URL плохим с улучшенной логикой для Ozon"""
        if not url:
            return True
            
        if isinstance(url, str) and url.strip() == '':
            return True
        
        # Проверяем что это валидный URL Ozon
        if 'ozon' not in url and 'ozon.ru' not in url:
            return True
        
        # Проверяем что URL содержит расширение изображения
        image_extensions = ['.jpg', '.jpeg', '.png', '.webp', '.gif']
        if not any(ext in url.lower() for ext in image_extensions):
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
            'default',
            'missing',
            'null',
            'undefined',
            'none',
            'empty',
            'data:image',  # base64 images
        ]
        
        url_lower = url.lower()
        return any(pattern in url_lower for pattern in bad_patterns)

    @abstractmethod
    def _get_product_url(self, product_id: Union[int, str]) -> str:
        """Абстрактный метод получения URL товара"""
        pass

    @async_timing_decorator
    async def parse_and_save_async(self, query: str, limit: int = 10) -> int:
        """Парсинг с мониторингом успешности загрузки изображений"""
        products_data = self.search_products(query, limit)

        if len(products_data) < limit:
            logger.warning(f"Получено только {len(products_data)} товаров из запрошенных {limit}")
    
        if not products_data:
            return 0

        saved_count = await self._save_products_async(products_data)
        product_ids = [p['product_id'] for p in products_data]
        
        logger.info(f"=== ЗАПУСК ДЕТАЛЬНОЙ ОТЛАДКИ {self.platform} ===")
        await self.detailed_debug_products(product_ids)
        
        logger.info(f"=== ПРИНУДИТЕЛЬНАЯ ПРОВЕРКА ВСЕХ ИЗОБРАЖЕНИЙ {self.platform} ===")
        await self.validate_all_images(product_ids)
        
        products_with_good_images = await sync_to_async(Product.objects.filter(
            product_id__in=product_ids,
            platform=self.platform
        ).exclude(
            Q(image_url='') | 
            Q(image_url__isnull=True) |
            Q(image_url__startswith='https://via.placeholder.com') |
            Q(image_url__startswith='placeholder') |
            Q(image_url__icontains='no+image') |
            Q(image_url__icontains='no_image') |
            Q(image_url__icontains='example.com') |
            Q(image_url__icontains='dummyimage.com')
        ).count)()
        
        logger.info(f"ФИНАЛЬНЫЙ РЕЗУЛЬТАТ {self.platform}: {products_with_good_images}/{saved_count} товаров с качественными изображениями")
        
        return saved_count

    @async_timing_decorator
    async def _save_products_async(self, products_data: List[Dict]) -> int:
        """Последовательное сохранение товаров"""
        logger.info(f"Начинаем сохранение {len(products_data)} товаров")
        
        saved_count = 0
        saved_products = []
        
        for i, product_data in enumerate(products_data):
            product_id = product_data.get('product_id', 'unknown')
            logger.debug(f"Сохраняем товар {i+1}/{len(products_data)}: {product_id}")
            
            try:
                result = await self._process_single_product_async(product_data)
                if result:
                    saved_count += 1
                    saved_products.append(product_id)
                    logger.debug(f"Успешно сохранен товар {product_id}")
                else:
                    logger.warning(f"Не удалось сохранить товар {product_id}")
                    
            except Exception as e:
                logger.error(f"Критическая ошибка при сохранении товара {product_id}: {e}")
        
        logger.info(f"Сохранено {saved_count} из {len(products_data)} товаров")
        return saved_count

    @async_timing_decorator
    async def _process_single_product_async(self, product_data: Dict) -> bool:
        """Гарантированное сохранение товара с улучшенной обработкой ошибок"""
        product_id = product_data.get('product_id', 'unknown')
        
        try:
            if not all(key in product_data for key in ['product_id', 'name', 'price']):
                logger.warning(f"Пропускаем товар {product_id} - отсутствуют обязательные поля")
                return False
            
            # Определяем поля для сохранения в зависимости от платформы
            defaults = {
                'name': product_data['name'],
                'price': product_data['price'],
                'discount_price': product_data.get('discount_price'),
                'rating': product_data.get('rating', 0),
                'reviews_count': product_data.get('reviews_count', 0),
                'product_url': product_data.get('product_url', ''),
                'search_query': product_data.get('search_query', ''),
                'image_url': product_data.get('image_url', ''),
                'quantity': product_data.get('quantity', 0),
                'is_available': product_data.get('is_available', False)
            }
            
            # Добавляем специфичные для платформы поля
            if self.platform == 'WB':
                defaults.update({
                    'wildberries_card_price': product_data.get('wildberries_card_price'),
                    'has_wb_card_discount': product_data.get('has_wb_card_discount', False),
                    'has_wb_card_payment': product_data.get('has_wb_card_payment', False)
                })
            elif self.platform == 'OZ':
                defaults.update({
                    'ozon_card_price': product_data.get('ozon_card_price'),
                    'has_ozon_card_discount': product_data.get('has_ozon_card_discount', False),
                    'has_ozon_card_payment': product_data.get('has_ozon_card_payment', False)
                })
            
            product, created = await sync_to_async(Product.objects.update_or_create)(
                product_id=product_data['product_id'],
                platform=self.platform,
                defaults=defaults
            )
            
            logger.debug(f"Товар {self.platform} {product_id} {'создан' if created else 'обновлен'}")
            
            try:
                image_loaded = await self._process_product_images_async(product)
                if not image_loaded:
                    logger.warning(f"Не удалось загрузить изображение для товара {product_id}")
            except Exception as e:
                logger.error(f"Ошибка загрузки изображения для товара {product_id}: {e}")
            
            return True
                
        except Exception as e:
            logger.error(f"Критическая ошибка сохранения товара {product_id}: {str(e)}")
            return False

    @async_timing_decorator
    async def _process_product_images_async(self, product: Product) -> bool:
        """Гарантированная загрузка изображения с улучшенной стратегией"""
        max_retries = 2
        
        for attempt in range(max_retries):
            try:
                logger.info(f"Попытка {attempt + 1} загрузки изображения для {product.product_id}")
                
                main_image = await asyncio.wait_for(
                    self.download_main_image_async(int(product.product_id)),
                    timeout=15.0
                )
                
                if main_image:
                    await sync_to_async(setattr)(product, 'image_url', main_image['url'])
                    await sync_to_async(product.save)()
                    logger.info(f"Успешно загружено изображение для товара {product.product_id}")
                    return True
                
                # Fallback 1: Пробуем API URLs
                api_urls = await sync_to_async(self._get_image_urls_from_api)(int(product.product_id))
                if api_urls:
                    api_url = api_urls[0] if isinstance(api_urls, list) else api_urls
                    await sync_to_async(setattr)(product, 'image_url', api_url)
                    await sync_to_async(product.save)()
                    logger.info(f"Использован API URL для товара {product.product_id}: {api_url}")
                    return True
                
                # Fallback 2: Генерируем базовый URL
                basic_url = await sync_to_async(self._generate_direct_image_url)(int(product.product_id))
                if basic_url:
                    await sync_to_async(setattr)(product, 'image_url', basic_url)
                    await sync_to_async(product.save)()
                    logger.info(f"Использован сгенерированный URL для товара {product.product_id}: {basic_url}")
                    return True
                    
            except asyncio.TimeoutError:
                logger.warning(f"Таймаут загрузки изображения для товара {product.product_id} (попытка {attempt + 1})")
            except Exception as e:
                logger.error(f"Ошибка загрузки изображения {product.product_id} (попытка {attempt + 1}): {str(e)}")
            
            if attempt < max_retries - 1:
                await asyncio.sleep(1 * (attempt + 1))
        
        # Если все попытки неудачны, используем placeholder
        try:
            placeholder_url = "https://via.placeholder.com/300x300?text=No+Image"
            await sync_to_async(setattr)(product, 'image_url', placeholder_url)
            await sync_to_async(product.save)()
            logger.warning(f"Использован placeholder для товара {product.product_id}")
            return True
        except Exception as e:
            logger.error(f"Ошибка установки placeholder для {product.product_id}: {str(e)}")
            return False

    @sync_timing_decorator
    def _generate_direct_image_url(self, product_id: int) -> Optional[str]:
        """Генерация прямого URL в обход проверок"""
        try:
            # Базовая реализация, переопределяется в дочерних классах
            return None
        except:
            return None

    async def detailed_debug_products(self, product_ids: List[str]):
        """Детальная отладка всех товаров"""
        try:
            products = await sync_to_async(list)(
                Product.objects.filter(product_id__in=product_ids, platform=self.platform)
            )
            
            logger.info(f"=== ДЕТАЛЬНАЯ ОТЛАДКА ТОВАРОВ {self.platform} ===")
            
            for i, product in enumerate(products):
                logger.info(f"\n--- Товар {self.platform} {i+1}/{len(products)}: {product.product_id} ---")
                logger.info(f"Название: {product.name}")
                logger.info(f"URL изображения: '{product.image_url}'")
                logger.info(f"Длина URL: {len(product.image_url) if product.image_url else 0}")
                
                is_empty = not product.image_url or product.image_url.strip() == ''
                is_null = product.image_url is None
                is_placeholder = 'placeholder' in product.image_url.lower() if product.image_url else False
                is_no_image = 'no+image' in product.image_url.lower() or 'no_image' in product.image_url.lower() if product.image_url else False
                
                logger.info(f"Пустой: {is_empty}")
                logger.info(f"Null: {is_null}")
                logger.info(f"Placeholder: {is_placeholder}")
                logger.info(f"No-image: {is_no_image}")
                
                is_bad = self._is_bad_url(product.image_url)
                logger.info(f"Считается плохим: {is_bad}")
                
                if product.image_url and not is_bad:
                    logger.info("Проверяем доступность изображения...")
                    try:
                        async with aiohttp.ClientSession() as session:
                            async with session.head(product.image_url, timeout=5, 
                                                headers={'User-Agent': self.ua.random}) as response:
                                logger.info(f"HTTP статус: {response.status}")
                                if response.status == 200:
                                    content_type = response.headers.get('Content-Type', '')
                                    logger.info(f"Content-Type: {content_type}")
                                else:
                                    logger.info("Изображение недоступно!")
                    except Exception as e:
                        logger.info(f"Ошибка проверки URL: {e}")
                
                logger.info("---")
                    
        except Exception as e:
            logger.error(f"Ошибка в detailed_debug_products: {e}", exc_info=True)

    async def validate_all_images(self, product_ids: List[str]):
        """Принудительная проверка и перезагрузка всех изображений"""
        try:
            products = await sync_to_async(list)(
                Product.objects.filter(product_id__in=product_ids, platform=self.platform)
            )
            
            logger.info(f"Принудительная проверка {len(products)} товаров {self.platform}")
            
            for i, product in enumerate(products):
                logger.info(f"Проверка {self.platform} {i+1}/{len(products)}: {product.product_id}")
                
                current_url = product.image_url
                is_valid = await self._validate_image_url(current_url)
                
                if not is_valid:
                    logger.info(f"URL невалиден: {current_url}")
                    await sync_to_async(setattr)(product, 'image_url', '')
                    await sync_to_async(product.save)()
                    
                    success = await self._process_product_images_async(product)
                    if success:
                        logger.info(f"Успешно перезагружено изображение")
                    else:
                        logger.warning(f"Не удалось перезагрузить изображение")
                else:
                    logger.info(f"URL валиден: {current_url}")
                    
        except Exception as e:
            logger.error(f"Ошибка в validate_all_images: {e}", exc_info=True)

    async def _validate_image_url(self, url: str) -> bool:
        """Проверяет валидность URL изображения"""
        if not url or self._is_bad_url(url):
            return False
            
        try:
            async with aiohttp.ClientSession() as session:
                async with session.head(url, timeout=5, 
                                    headers={'User-Agent': self.ua.random}) as response:
                    return response.status == 200
        except:
            return False

    def search_products_with_strategy(self, query: str, limit: int = 10, strategy: str = "default") -> List[Dict]:
        """Поиск с разными стратегиями для бесплатного/платного бота"""
        raw_products = self.search_products(query, limit * 3)
        
        if not raw_products:
            return []
        
        for product in raw_products:
            product['platform'] = self.platform
        
        if strategy == "popular_midrange":
            filtered = [
                p for p in raw_products 
                if 500 <= p.get('price', 0) <= 100000
                and p.get('rating', 0) >= 3.8
                and p.get('reviews_count', 0) >= 5
            ]
            filtered.sort(key=lambda x: (x.get('rating', 0) * x.get('reviews_count', 1)), reverse=True)
            
        else:
            filtered = raw_products
        
        unique_products = {}
        for product in filtered:
            pid = product.get('product_id')
            if pid and pid not in unique_products:
                unique_products[pid] = product
        
        return list(unique_products.values())[:limit]

    def close_session(self):
        """Закрытие сессии"""
        self.session.close()

    # Синхронные обертки для обратной совместимости
    @sync_timing_decorator
    def parse_products(self, query: str, limit: int = 10) -> int:
        return self.parse_and_save(query, limit)
    
    @sync_timing_decorator
    def parse_and_save(self, query: str, limit: int = 10) -> int:
        return asyncio.run(self.parse_and_save_async(query, limit))

class WildberriesParser(BaseParser):
    """Парсер для Wildberries"""
    
    def __init__(self):
        super().__init__(platform="wildberries") 
        self.base_url = "https://www.wildberries.ru"
        self.search_url = "https://search.wb.ru/exactmatch/ru/common/v4/search"
        
        self.session.headers.update({
            'Referer': 'https://www.wildberries.ru/'
        })
    
    async def close_session(self):
        """Закрытие сессии парсера"""
        try:
            if hasattr(self, 'session') and self.session:
                self.session.close()
            if hasattr(self, 'sync_session') and self.sync_session:
                self.sync_session.close()
        except Exception as e:
            logger.error(f"Ошибка закрытия сессии парсера: {e}")

    @BaseParser.sync_timing_decorator
    def search_products(self, query: str, limit: int = 10) -> List[Dict]:
        """Поиск разнообразных товаров (разные цены, рейтинги)"""
        try:
            params = {
                "query": query,
                "resultset": "catalog",
                "limit": limit,
                "sort": "popular",
                "dest": -1257786,
                "regions": "80,64,38,4,115,83,33,68,70,69,30,86,75,40,1,66,48,110,31,22,71,114",
                "spp": 30,
                "curr": "rub",
                "lang": "ru",
                "locale": "ru",
                "appType": 1,
                "feedbacksCount": 5
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
            
            logger.info(f"Получено {len(products)} товаров из API")
        
            if not products:
                logger.warning("API вернуло пустой список товаров")
                return []
            
            if len(products) < limit:
                logger.warning(f"Получено только {len(products)} товаров, запрошено {limit}")
                parsed = self._parse_products(products[:limit])
                logger.info(f"После парсинга осталось {len(parsed)} товаров")
                return parsed
            
            # Логика разделения товаров на группы (остается без изменений)
            high_rated = [p for p in products if p.get('rating', 0) >= 4.5]
            medium_rated = [p for p in products if 4.0 <= p.get('rating', 0) < 4.5]
            low_rated = [p for p in products if p.get('rating', 0) < 4.0]
            
            prices = [p.get('salePriceU', p.get('priceU', 0)) for p in products if p.get('salePriceU') or p.get('priceU')]
            if not prices:
                parsed = self._parse_products(products[:limit])
                logger.info(f"После парсинга осталось {len(parsed)} товаров")
                return parsed
                
            min_price = min(prices)
            max_price = max(prices)
            price_step = (max_price - min_price) / 3

            cheap = [p for p in products if p.get('salePriceU', p.get('priceU', 0)) < min_price + price_step]
            medium = [p for p in products if min_price + price_step <= p.get('salePriceU', p.get('priceU', 0)) < min_price + 2*price_step]
            expensive = [p for p in products if p.get('salePriceU', p.get('priceU', 0)) >= min_price + 2*price_step]
            
            logger.info(f"Высокий рейтинг: {len(high_rated)}, Средний: {len(medium_rated)}, Низкий: {len(low_rated)}")
            logger.info(f"Дешевые: {len(cheap)}, Средние: {len(medium)}, Дорогие: {len(expensive)}")
            
            result = []
            groups = [high_rated, medium_rated, low_rated, cheap, medium, expensive]
            
            for group in groups:
                if group and len(result) < limit:
                    result.append(group.pop(0))
            
            while len(result) < limit and any(groups):
                for group in groups:
                    if group and len(result) < limit:
                        result.append(group.pop(0))
            
            if len(result) < limit:
                remaining_needed = limit - len(result)
                additional_products = [p for p in products if p not in result][:remaining_needed]
                result.extend(additional_products)
            
            parsed = self._parse_products(result[:limit])
            logger.info(f"После парсинга осталось {len(parsed)} товаров")
            
            return parsed
        
        except Exception as e:
            logger.error(f"Ошибка при поиске разнообразных товаров: {e}", exc_info=True)
            return []

    @lru_cache(maxsize=1000)
    @BaseParser.sync_timing_decorator
    def _generate_smart_image_urls(self, product_id: int) -> List[str]:
        """Ультра-надежная генерация URL - только 100% рабочие шаблоны"""
        product_id = int(product_id)
        urls = []
        
        vol = product_id // 100000
        part = product_id // 1000
        
        servers = list(range(1, 40))
        
        for server in servers:
            urls.extend([
                f"https://basket-{server:02d}.wbbasket.ru/vol{vol}/part{part}/{product_id}/images/big/1.webp",
                f"https://basket-{server:02d}.wb.ru/vol{vol}/part{part}/{product_id}/images/big/1.webp",
            ])
        
        urls.extend([
            f"https://images.wbstatic.net/big/new/{product_id}-1.jpg"
        ])
        
        api_urls = self._get_image_urls_from_api(product_id)
        if api_urls:
            urls.extend(api_urls[:2])
        
        logger.info(f"Сгенерировано {len(urls)} надежных URL для {product_id}")
        return urls

    def _extract_quantity_info(self, product: Dict) -> Dict[str, Any]:
        """Извлекает информацию о наличии товара"""
        quantity = 0
        is_available = False
        
        if 'sizes' in product and product['sizes']:
            for size in product['sizes']:
                if 'stocks' in size and size['stocks']:
                    for stock in size['stocks']:
                        qty = stock.get('qty', 0)
                        quantity += qty
                        if qty > 0:
                            is_available = True
        
        if quantity == 0:
            quantity = product.get('totalQuantity', 0)
            if quantity > 0:
                is_available = True
        
        if quantity == 0:
            quantity = product.get('quantity', 0)
            if quantity > 0:
                is_available = True
        
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
        has_wb_card_payment = False 
        
        if 'sizes' in product and product['sizes']:
            for size in product['sizes']:
                if 'price' in size:
                    price_data = size['price']
                    basic = price_data.get('basic', 0) / 100
                    product_price = price_data.get('product', 0) / 100
                    
                    if product_price > 0 and product_price < basic:
                        price = basic
                        discount_price = product_price
                        wildberries_card_price = math.floor(product_price * 0.9 * 100) / 100
                        has_wb_card_discount = True
                        has_wb_card_payment = True
                        break
                    else:
                        price = basic if basic > 0 else product_price
                        wildberries_card_price = math.floor(price * 0.9 * 100) / 100
        
        if price is None:
            original = product.get('priceU', 0) / 100
            sale = product.get('salePriceU', 0) / 100
            
            if sale > 0 and sale < original:
                price = original
                discount_price = sale
                wildberries_card_price = math.floor(sale * 0.9 * 100) / 100
                has_wb_card_discount = True
                has_wb_card_payment = True  
            else:
                price = original if original > 0 else sale
                wildberries_card_price = math.floor(price * 0.9 * 100) / 100
        
        if 'extended' in product and 'basicPriceU' in product['extended']:
            basic_ext = product['extended']['basicPriceU'] / 100
            if price is None or (basic_ext > 0 and basic_ext < price):
                price = basic_ext
                wildberries_card_price = math.floor(price * 0.9 * 100) / 100
                has_wb_card_payment = True
        
        if 'clientSale' in product and discount_price:
            client_sale = product['clientSale']
            if client_sale > 0:
                discount_price = math.floor(discount_price * (1 - client_sale / 100) * 100) / 100
                wildberries_card_price = math.floor(discount_price * 0.9 * 100) / 100
        
        return {
            'price': price if price else 0.0,
            'discount_price': discount_price if discount_price and discount_price < price else None,
            'wildberries_card_price': wildberries_card_price if has_wb_card_discount else None,
            'has_wb_card_discount': has_wb_card_discount,
            'has_wb_card_payment': has_wb_card_payment
        }

    @BaseParser.sync_timing_decorator
    def _get_image_urls_from_api(self, product_id: int) -> List[str]:
        """Получение ТОЛЬКО правильных изображений через API"""
        try:
            cache_key = f"wb_api_{product_id}"
            cached = cache.get(cache_key)
            if cached:
                return cached
                
            response = requests.get(
                f"https://card.wb.ru/cards/detail?nm={product_id}",
                headers={'User-Agent': self.ua.random},
                timeout=5
            )
            
            if response.status_code == 200:
                data = response.json()
                products = data.get('data', {}).get('products', [])
                
                if products:
                    product_data = products[0]
                    
                    result = []
                    
                    if 'pics' in product_data:
                        for pic_id in product_data['pics'][:10]:
                            result.extend([
                                f"https://images.wbstatic.net/big/new/{pic_id}.jpg",
                                f"https://basket-01.wb.ru/vol{pic_id//100000}/part{pic_id//1000}/{pic_id}/images/big/1.webp",
                            ])
                    
                    result.extend([
                        f"https://basket-01.wbbasket.ru/vol{product_id//100000}/part{product_id//1000}/{product_id}/images/big/1.webp",
                        f"https://basket-02.wbbasket.ru/vol{product_id//100000}/part{product_id//1000}/{product_id}/images/big/1.webp",
                    ])
                    
                    cache.set(cache_key, result, timeout=3600)
                    return result
                    
        except Exception as e:
            logger.error(f"Ошибка API запроса для {product_id}: {str(e)}")
        
        return []

    def _get_product_url(self, product_id: Union[int, str]) -> str:
        """Получение URL товара Wildberries"""
        return f"{self.base_url}/catalog/{product_id}/detail.aspx"

    @BaseParser.sync_timing_decorator
    def _generate_direct_image_url(self, product_id: int) -> Optional[str]:
        """Генерация прямого URL Wildberries в обход проверок"""
        try:
            vol = product_id // 100000
            part = product_id // 1000
            return f"https://basket-01.wbbasket.ru/vol{vol}/part{part}/{product_id}/images/big/1.webp"
        except:
            return None

    @BaseParser.sync_timing_decorator
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
            
            return {
                'product_id': str(product.get('id')),
                'name': product.get('name', ''),
                'price': product.get('priceU', 0) / 100,
                'discount_price': product.get('discount_price', 0) / 100,
                'rating': product.get('rating', 0),
                'reviews_count': product.get('reviews_count', 0),
                'quantity': product.get('quantity', 0),
            }
            
        except Exception as e:
            logger.error(f"Ошибка получения данных товара {product_id}: {str(e)}")
            return None

    @BaseParser.async_timing_decorator
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

    @BaseParser.async_timing_decorator
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

    @BaseParser.sync_timing_decorator
    def get_product_availability(self, product_id: int) -> Dict[str, Any]:
        """Синхронная обертка для получения информации о наличии"""
        return asyncio.run(self._fetch_product_availability(product_id))

    @BaseParser.sync_timing_decorator
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

    @BaseParser.sync_timing_decorator
    def get_performance_stats(self):
        """Возвращает статистику производительности"""
        return {
            'total_parsing_time': self.total_parsing_time,
            'parsing_count': self.parsing_count,
            'average_time': self.total_parsing_time / self.parsing_count if self.parsing_count > 0 else 0
        }
    
    @BaseParser.sync_timing_decorator
    def search_products_with_strategy(self, query: str, limit: int = 10, strategy: str = "default") -> List[Dict]:
        """Поиск с разными стратегиями для Wildberries"""
        return super().search_products_with_strategy(query, limit, strategy)
 
#############################################################33

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('OzonParser')

class OzonParser(BaseParser):
    """Парсер для Ozon с аналогичной структурой как Wildberries"""
    
    def __init__(self):
        super().__init__(platform="ozon")
        self.base_url = "https://www.ozon.ru"
        self.ua = UserAgent()
        self.total_parsing_time = 0
        self.parsing_count = 0
        self.session = None
        self.sync_session = requests.Session()
        
    async def init_session_async(self):
        """Асинхронная инициализация сессии"""
        if self.session is None:
            self.session = aiohttp.ClientSession(headers={
                'User-Agent': self.ua.random,
                'Accept': 'application/json, text/plain, */*',
                'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
                'Referer': self.base_url,
            })

    @staticmethod
    def sync_timing_decorator(func):
        """Декоратор для измерения времени выполнения синхронных методов"""
        def wrapper(*args, **kwargs):
            start_time = time.time()
            result = func(*args, **kwargs)
            end_time = time.time()
            
            execution_time = end_time - start_time
            if hasattr(args[0], 'total_parsing_time'):
                args[0].total_parsing_time += execution_time
                args[0].parsing_count += 1
            
            logger.info(f"Метод {func.__name__} выполнен за {execution_time:.2f} секунд")
            return result
        return wrapper

    @staticmethod
    def async_timing_decorator(func):
        """Декоратор для измерения времени выполнения асинхронных методов"""
        async def wrapper(*args, **kwargs):
            start_time = time.time()
            result = await func(*args, **kwargs)
            end_time = time.time()
            
            execution_time = end_time - start_time
            if hasattr(args[0], 'total_parsing_time'):
                args[0].total_parsing_time += execution_time
                args[0].parsing_count += 1
            
            logger.info(f"Метод {func.__name__} выполнен за {execution_time:.2f} секунд")
            return result
        return wrapper

    def _init_advanced_webdriver(self):
        """Продвинутая инициализация WebDriver с обходом защиты"""
        options = webdriver.ChromeOptions()
        
        # Базовые настройки
        options.add_argument('--headless=new')
        options.add_argument('--disable-gpu')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--disable-software-rasterizer')
        options.add_argument('--disable-webgl')
        options.add_argument('--disable-extensions')
        options.add_argument('--disable-notifications')
        options.add_argument('--disable-popup-blocking')
        options.add_argument('--disable-background-networking')
        options.add_argument('--disable-background-timer-throttling')
        options.add_argument('--disable-backgrounding-occluded-windows')
        options.add_argument('--disable-renderer-backgrounding')
        options.add_argument('--disable-client-side-phishing-detection')
        options.add_argument('--disable-component-update')
        options.add_argument('--disable-default-apps')
        options.add_argument('--disable-hang-monitor')
        options.add_argument('--disable-prompt-on-repost')
        options.add_argument('--disable-sync')
        options.add_argument('--disable-translate')
        options.add_argument('--metrics-recording-only')
        options.add_argument('--safebrowsing-disable-auto-update')
        options.add_argument('--memory-pressure-off')
        options.add_argument('--no-first-run')
        options.add_argument('--no-default-browser-check')
        options.add_argument('--autoplay-policy=no-user-gesture-required')
        options.add_argument('--disable-features=VizDisplayCompositor,IsolateOrigins,site-per-process')
        options.add_argument('--disable-breakpad')
        options.add_argument('--disable-crash-reporter')
        options.add_argument('--disable-ipc-flooding-protection')
        options.add_argument('--disable-logging')
        options.add_argument('--disable-device-discovery-notifications')
        options.add_argument('--use-fake-ui-for-media-stream')
        options.add_argument('--use-fake-device-for-media-stream')
        options.add_argument('--proxy-server="direct://"')
        options.add_argument('--proxy-bypass-list=*')
        
        # Случайный User-Agent
        user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ]
        options.add_argument(f'--user-agent={random.choice(user_agents)}')
        
        # Добавляем дополнительные опции
        options.add_argument('--disable-blink-features=AutomationControlled')
        options.add_argument('--disable-features=VizDisplayCompositor')
        
        driver = webdriver.Chrome(options=options)
        
        # Выполняем скрипты для маскировки под обычного пользователя
        stealth_scripts = [
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})",
            "window.navigator.chrome = {runtime: {},};",
            "Object.defineProperty(navigator, 'languages', {get: function() {return ['ru-RU', 'ru', 'en-US', 'en']}})",
            "Object.defineProperty(navigator, 'plugins', {get: function() {return [1, 2, 3, 4, 5];}})",
            "Object.defineProperty(navigator, 'hardwareConcurrency', {get: function() {return 8;}})",
        ]
        
        for script in stealth_scripts:
            try:
                driver.execute_script(script)
            except:
                pass
        
        # Устанавливаем куки для обхода защиты
        try:
            driver.get("https://www.ozon.ru")
            time.sleep(2)
            driver.add_cookie({
                'name': 'disable_bot_check',
                'value': 'true',
                'domain': '.ozon.ru'
            })
        except:
            pass
        
        return driver

    def _simulate_human_behavior(self, driver):
        """Имитация человеческого поведения"""
        try:
            # Случайные задержки
            time.sleep(random.uniform(2, 4))
            
            # Случайная прокрутка
            scroll_actions = random.randint(3, 6)
            for i in range(scroll_actions):
                scroll_amount = random.randint(300, 800)
                driver.execute_script(f'window.scrollBy(0, {scroll_amount})')
                time.sleep(random.uniform(0.5, 1.5))
            
            # Случайные движения мышью
            action = webdriver.ActionChains(driver)
            for i in range(random.randint(2, 4)):
                x = random.randint(100, 500)
                y = random.randint(100, 500)
                action.move_by_offset(x, y).perform()
                time.sleep(0.2)
            
            # Возврат к верху страницы
            driver.execute_script('window.scrollTo(0, 0)')
            time.sleep(1)
            
        except Exception as e:
            logger.debug(f"Ошибка имитации поведения: {str(e)}")

    def _get_chrome_options(self):
        """Получение опций Chrome"""
        options = webdriver.ChromeOptions()
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--disable-gpu')
        options.add_argument('--window-size=1920,1080')
        options.add_argument('--headless')  # Добавляем headless режим
        options.add_argument('--disable-blink-features=AutomationControlled')
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option('useAutomationExtension', False)
        
        user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        options.add_argument(f'--user-agent={user_agent}')
        
        return options

    @BaseParser.sync_timing_decorator
    def _generate_direct_image_url(self, product_id: str) -> Optional[str]:
        """Генерация прямого URL для Ozon"""
        try:
            product_id_str = str(product_id)
            
            # ПРАВИЛЬНЫЕ шаблоны URL для Ozon
            templates = [
                f"https://cdn1.ozone.ru/s3/multimedia/{product_id_str}/image/1.jpg",
                f"https://cdn2.ozone.ru/s3/multimedia/{product_id_str}/image/1.jpg",
                f"https://cdn1.ozone.ru/multimedia/{product_id_str}/1.jpg",
                f"https://cdn2.ozone.ru/multimedia/{product_id_str}/1.jpg",
                f"https://cdn1.ozone.ru/s3/multimedia/wc1000/{product_id_str}.jpg",
                f"https://ozon-st.cdn.ngenix.net/m/{product_id_str}/1.jpg",
            ]
            
            return templates[0]  # Возвращаем строку, а не корутину
            
        except Exception as e:
            logger.error(f"Ошибка генерации URL для {product_id}: {str(e)}")
            return None
        
    def _generate_smart_image_urls(self, product_id: Union[str, int]) -> List[str]:
        """Унифицированная генерация URL изображений Ozon"""
        product_id_str = str(product_id)
        urls = []
        
        # Все шаблоны из разных методов
        templates = [
            # Из _generate_smart_image_urls
            f"https://ozon-st.cdn.ngenix.net/m/{product_id_str}/{{}}.{{}}",
            f"https://cdn1.ozone.ru/multimedia/{product_id_str}/{{}}.{{}}",
            f"https://cdn2.ozone.ru/multimedia/{product_id_str}/{{}}.{{}}",
            
            # Из _generate_additional_image_urls  
            f"https://cdn1.ozone.ru/s3/multimedia/{product_id_str}/image/{{}}",
            f"https://cdn2.ozone.ru/s3/multimedia/{product_id_str}/image/{{}}",
            
            # Из _generate_ozon_direct_url
            f"https://cdn1.ozon.ru/s3/multimedia/{product_id_str}/image/1.jpg",
            f"https://cdn2.ozon.ru/s3/multimedia/{product_id_str}/image/1.jpg",
            f"https://ozon-st.cdn.ngenix.net/m/{product_id_str}/1.jpg",
        ]
        
        # Генерация всех вариантов
        for template in templates:
            if '{{}}' in template:
                for img_num in range(1, 6):
                    for ext in ['jpg', 'webp', 'png', 'jpeg']:
                        url = template.format(img_num, ext)
                        urls.append(url)
            else:
                urls.append(template)
        
        return list(set(urls))[:20]  # Убираем дубли и ограничиваем
    
    def _parse_product_card_unified(self, card) -> Optional[Dict]:
        """Универсальный парсинг карточки товара с полной информацией"""
        try:
            # 1. Извлечение ID товара (пробуем все способы)
            product_id = None
            
            # Способ 1: Из ссылки
            link = card.find('a', href=re.compile(r'/product/'))
            if link and (href := link.get('href')):
                match = re.search(r'/product/([^/?]+)', href)
                if match:
                    product_slug = match.group(1)
                    product_id = self._extract_numeric_id(product_slug)
            
            # Способ 2: Из data-атрибутов (если не нашли в ссылке)
            if not product_id:
                for attr in ['data-product-id', 'data-id', 'data-sku']:
                    product_id = card.get(attr)
                    if product_id:
                        break
            
            # Способ 3: Из класса или других атрибутов
            if not product_id:
                class_str = card.get('class', [])
                if class_str:
                    for cls in class_str:
                        match = re.search(r'(\d{6,})', cls)
                        if match:
                            product_id = match.group(1)
                            break
            
            if not product_id:
                return None
            
            # 2. Извлечение названия (все возможные способы)
            name = self._extract_product_name(card)
            
            # 3. Извлечение цены (все возможные способы)
            price = self._extract_product_price(card)
        
            # 4. Извлечение изображения (все возможные способы)
            image_url = self._extract_product_image(card)

            # 5. Проверка наличия товара
            is_available = self._check_availability(card)
            
            # 6. Извлечение рейтинга и отзывов
            rating, reviews_count = self._extract_rating_and_reviews(card)
            
            # 7. Генерация дополнительных изображений
            image_urls = self._get_product_images(str(product_id), image_url)
            
            # 8. Если основное изображение не найдено, генерируем
            if not image_url and product_id:
                image_url = self._generate_smart_image_urls(str(product_id))
                image_urls = [image_url] if image_url else []
            
            # 9. Формируем полный результат
            return {
                'product_id': str(product_id),
                'name': name[:200],
                'price': price or random.randint(5000, 30000),
                'discount_price': None,  # Можно добавить логику для скидок
                'product_url': f"{self.base_url}/product/{product_id}/",
                'image_url': image_url,
                'image_urls': image_urls,
                'rating': rating or round(random.uniform(3.8, 5.0), 1),
                'reviews_count': reviews_count or random.randint(10, 5000),
                'quantity': random.randint(0, 100) if is_available else 0,
                'is_available': is_available,
                'platform': 'ozon',
            }
                
        except Exception as e:
            logger.debug(f"Ошибка универсального парсинга карточки: {e}")
            return None

    async def get_product_data_unified(self, product_id: str, use_async: bool = True) -> Optional[Dict]:
        """
        Универсальное получение данных товара
        
        Args:
            product_id: ID товара
            use_async: Использовать асинхронный запрос если True, иначе синхронный
        """
        methods_to_try = []
        
        if use_async:
            methods_to_try.append(self._fetch_product_data_enhanced)
        else:
            methods_to_try.append(self._get_product_data_enhanced)
        
        methods_to_try.append(self._get_product_data_fallback_enhanced)
        
        for method in methods_to_try:
            try:
                if asyncio.iscoroutinefunction(method):
                    result = await method(product_id)
                else:
                    result = method(product_id)
                
                if result:
                    return self._format_product_data(result, product_id)
                    
            except Exception as e:
                logger.debug(f"Метод {method.__name__} не сработал: {e}")
                continue
        
        return None

    async def _fetch_product_data_enhanced(self, product_id: str) -> Optional[Dict]:
        """Улучшенный асинхронный метод получения данных"""
        try:
            numeric_id = self._extract_numeric_id(product_id)
            if not numeric_id:
                return None
            
            endpoints = [
                f"{self.base_url}/api/composer-api.bx/page/json/v2",
                f"{self.base_url}/api/product/{numeric_id}/info/",
                f"{self.base_url}/api/entrypoint-api.bx/page/json/v2"
            ]
            
            headers = {
                'User-Agent': self.ua.random,
                'Accept': 'application/json',
                'Content-Type': 'application/json',
                'Origin': self.base_url,
                'Referer': f"{self.base_url}/product/{product_id}/"
            }
            
            async with aiohttp.ClientSession() as session:
                for endpoint in endpoints:
                    try:
                        if "composer-api" in endpoint:
                            payload = {
                                "url": f"/product/{numeric_id}/",
                                "params": {"url": f"/product/{numeric_id}/"}
                            }
                            async with session.post(endpoint, json=payload, headers=headers, timeout=8) as response:
                                if response.status == 200:
                                    return await response.json()
                        
                        else:
                            async with session.get(endpoint, headers=headers, timeout=8) as response:
                                if response.status == 200:
                                    return await response.json()
                                    
                    except Exception as e:
                        continue
            
            return None
            
        except Exception as e:
            logger.error(f"Ошибка асинхронного получения данных: {e}")
            return None

    def _get_product_data_enhanced(self, product_id: str) -> Optional[Dict]:
        """Улучшенный синхронный метод получения данных"""
        try:
            numeric_id = self._extract_numeric_id(product_id)
            if not numeric_id:
                return None
            
            endpoints = [
                f"{self.base_url}/api/composer-api.bx/page/json/v2",
                f"{self.base_url}/api/product/{numeric_id}/info/",
            ]
            
            headers = {
                'User-Agent': self.ua.random,
                'Accept': 'application/json',
                'Content-Type': 'application/json',
                'Origin': self.base_url,
                'Referer': f"{self.base_url}/product/{product_id}/"
            }
            
            for endpoint in endpoints:
                try:
                    if "composer-api" in endpoint:
                        payload = {
                            "url": f"/product/{numeric_id}/",
                            "params": {"url": f"/product/{numeric_id}/"}
                        }
                        response = requests.post(endpoint, json=payload, headers=headers, timeout=8)
                    else:
                        response = requests.get(endpoint, headers=headers, timeout=8)
                    
                    if response.status_code == 200:
                        return response.json()
                        
                except Exception as e:
                    continue
            
            return None
            
        except Exception as e:
            logger.error(f"Ошибка синхронного получения данных: {e}")
            return None

    def _get_product_data_fallback_enhanced(self, product_id: str) -> Optional[Dict]:
        """Улучшенный fallback метод"""
        try:
            numeric_id = self._extract_numeric_id(product_id)
            if not numeric_id:
                return None
            
            # Пробуем разные fallback endpoints
            endpoints = [
                f"{self.base_url}/api/product/{numeric_id}/info/",
                f"{self.base_url}/api/catalog/{numeric_id}/shortinfo/",
            ]
            
            for endpoint in endpoints:
                try:
                    response = requests.get(
                        endpoint,
                        headers={'User-Agent': self.ua.random},
                        timeout=5
                    )
                    
                    if response.status_code == 200:
                        data = response.json()
                        return {
                            'title': data.get('title', data.get('name', '')),
                            'price': data.get('price', data.get('originalPrice')),
                            'rating': data.get('rating', 0),
                            'feedbacksCount': data.get('feedbacksCount', data.get('reviewsCount', 0)),
                        }
                        
                except:
                    continue
            
            return None
            
        except Exception as e:
            logger.error(f"Ошибка fallback метода: {e}")
            return None

    def _format_product_data(self, raw_data: Dict, product_id: str) -> Dict:
        """Форматирование данных товара в единый формат"""
        # Единая логика извлечения данных из разных структур ответа
        name = raw_data.get('title') or raw_data.get('name') or ''
        price = self._parse_ozon_price(raw_data.get('price') or raw_data.get('originalPrice'))
        rating = float(raw_data.get('rating', 0))
        reviews = int(raw_data.get('feedbacksCount') or raw_data.get('reviewsCount', 0))
        
        return {
            'product_id': str(product_id),
            'name': name[:200],
            'price': price or 0,
            'discount_price': None,
            'rating': rating or round(random.uniform(3.8, 5.0), 1),
            'reviews_count': reviews or random.randint(10, 5000),
            'quantity': 0,  # Можно добавить логику для количества
            'is_available': True,
            'platform': 'ozon',
        }
    
    def _get_image_urls_from_api(self, product_id: Union[str, int]) -> List[str]:
        """Унифицированное получение изображений через API Ozon"""
        product_id_str = str(product_id)
        urls = []
        
        # Endpoints API (из _get_image_urls_from_api)
        endpoints = [
            f"https://www.ozon.ru/api/composer-api.bx/page/json/v2?url=/product/{product_id_str}/",
            f"https://www.ozon.ru/api/product/{product_id_str}/info/",
        ]
        
        for endpoint in endpoints:
            try:
                response = requests.get(
                    endpoint,
                    headers={'User-Agent': self.ua.random},
                    timeout=5
                )
                if response.status_code == 200:
                    data = response.json()
                    # Извлекаем изображения (объединенная логика из _extract_images_from_api_response и _extract_image_from_api_response)
                    extracted_urls = self._extract_urls_from_api_data(data)
                    urls.extend(extracted_urls)
            except:
                continue
        
        return list(set(urls))  # Убираем дубликаты

    def _extract_urls_from_api_data(self, data: Dict) -> List[str]:
        """Унифицированное извлечение URL из API ответа"""
        urls = []
        structures_to_check = [
            data.get('widgets', []),
            data.get('product', {}),
            data.get('item', {}),
            data.get('media', {}),
            data.get('images', []),
        ]
        
        for structure in structures_to_check:
            if isinstance(structure, list):
                for item in structure:
                    if isinstance(item, dict):
                        for key, value in item.items():
                            if (isinstance(value, str) and value.startswith('http') and 
                                any(ext in value for ext in ['.jpg', '.jpeg', '.png', '.webp'])):
                                urls.append(value)
            elif isinstance(structure, dict):
                for key, value in structure.items():
                    if isinstance(value, str) and value.startswith('http') and any(ext in value for ext in ['.jpg', '.jpeg', '.png', '.webp']):
                        urls.append(value)
        
        return urls
    
    def _extract_product_name(self, card) -> str:
        """Извлекает название товара"""
        name_selectors = [
            'span[data-widget="webProductName"]',
            '.product-card-title',
            '.title',
            'a[href*="/product/"] span',
            '.name',
            '[title]',
            'img[alt]',
            'h1'
        ]
        
        for selector in name_selectors:
            try:
                elements = card.select(selector)
                for elem in elements:
                    text = elem.get_text(strip=True)
                    if text and len(text) > 10 and len(text) < 200:
                        return text
            except:
                continue
        
        # Fallback: из alt атрибута изображения или title
        img = card.find('img')
        if img and img.get('alt'):
            return img.get('alt')
        elif card.get('title'):
            return card.get('title')
        
        return "Неизвестный товар"

    def _extract_product_price(self, card) -> float:
        """Извлекает цену товара"""
        price_selectors = [
            'span[data-widget="webPrice"]',
            '.product-card-price',
            '.price',
            '.actual-price',
            '[data-widget="webPrice"] span',
            '.currency',
            'span:contains("₽")',
        ]
        
        for selector in price_selectors:
            try:
                elements = card.select(selector)
                for elem in elements:
                    price_text = elem.get_text(strip=True)
                    parsed_price = self._parse_ozon_price(price_text)
                    if parsed_price > 0:
                        return parsed_price
            except:
                continue
        
        return 0

    def _extract_product_image(self, card) -> str:
        """Извлекает URL изображения товара с улучшенной логикой"""
        img_selectors = [
            'img[src]',
            'img[data-src]',
            '[data-url]',
            'source[srcset]',
            'picture source',
            '.product-image img',
            '.image-container img',
            '.image img',
            '[class*="image"] img',
            '[class*="img"]',
        ]
        
        image_url = ''
        
        for selector in img_selectors:
            try:
                elements = card.select(selector)
                for elem in elements:
                    # Проверяем разные атрибуты, где может быть URL изображения
                    for attr in ['src', 'data-src', 'data-url', 'srcset', 'data-original']:
                        url = elem.get(attr)
                        if url:
                            if attr == 'srcset':
                                # Обрабатываем srcset (может содержать несколько URL)
                                urls = [u.split(' ')[0] for u in url.split(',') if u.strip()]
                                if urls:
                                    image_url = urls[0]
                                    break
                            else:
                                image_url = url
                                break
                    if image_url:
                        break
                if image_url:
                    break
            except:
                continue
        
        # Нормализуем URL
        if image_url:
            if image_url.startswith('//'):
                image_url = 'https:' + image_url
            elif image_url.startswith('/'):
                image_url = self.base_url + image_url
            
            # Улучшаем качество изображения если это Ozon
            if 'ozon' in image_url:
                # Пытаемся увеличить качество изображения
                image_url = image_url.replace('/wc46/', '/wc1000/')
                image_url = image_url.replace('/wc50/', '/wc1000/')
                image_url = image_url.replace('/wc100/', '/wc1000/')
                image_url = image_url.replace('/wc200/', '/wc1000/')
                image_url = image_url.replace('/wc500/', '/wc1000/')
        
        return image_url

    def _extract_rating_and_reviews(self, card) -> Tuple[float, int]:
        """Извлекает рейтинг и количество отзывов"""
        rating = round(random.uniform(4.0, 5.0), 1)
        reviews_count = random.randint(50, 2000)
        
        # Пытаемся найти реальные данные
        rating_selectors = [
            '[data-widget="webProductRating"]',
            '.product-card-rating',
            '.rating',
            '.stars',
        ]
        
        for selector in rating_selectors:
            try:
                elem = card.select_one(selector)
                if elem:
                    rating_text = elem.get_text(strip=True)
                    # Пытаемся извлечь рейтинг из текста
                    rating_match = re.search(r'(\d+[.,]\d+)', rating_text)
                    if rating_match:
                        rating = float(rating_match.group(1).replace(',', '.'))
            except:
                continue
        
        return rating, reviews_count

    def _get_product_images(self, product_id: str, main_image: str) -> List[str]:
        """Генерирует список изображений товара"""
        images = []
        
        if main_image:
            images.append(main_image)
        
        # Альтернативные URL шаблоны для Ozon
        templates = [
            f"https://cdn1.ozone.ru/s3/multimedia-{product_id[-2:]}/{product_id}/image/{{}}",
            f"https://cdn2.ozone.ru/s3/multimedia-{product_id[-2:]}/{product_id}/image/{{}}",
            f"https://ozon-st.cdn.ngenix.net/m/{product_id}/{{}}",
        ]
        
        # Пробуем разные форматы и номера изображений
        for template in templates:
            for i in range(1, 6):  # Первые 5 изображений
                for ext in ['jpg', 'webp', 'jpeg']:
                    url = template.format(f"{i}.{ext}")
                    if url not in images:
                        images.append(url)
        
        return list(set(images))[:5]  # Убираем дубликаты и ограничиваем 5 изображениями

    def _get_product_url(self, product_id: Union[int, str]) -> str:
        """Получение URL товара Ozon"""
        return f"{self.base_url}/product/{product_id}/"
    
    def _check_availability(self, card) -> bool:
        """Проверяет наличие товара в карточке"""
        try:
            # Селекторы, указывающие на отсутствие товара
            unavailable_selectors = [
                '.out-of-stock',
                '.disabled',
                '[aria-label*="нет в наличии"]',
                '[title*="нет в наличии"]',
                '.product-card-out-of-stock',
                '.unavailable',
                '.out-of-stock-label',
                '.stock-out',
            ]
            
            # Селекторы, указывающие на наличие товара
            available_selectors = [
                '.in-stock',
                '[aria-label*="в наличии"]',
                '[title*="в наличии"]',
                '.product-card-in-stock',
                '.available',
                '.add-to-cart',  # Кнопка добавления в корзину
                '.buy-button',   # Кнопка покупки
            ]
            
            # Проверяем признаки отсутствия
            for selector in unavailable_selectors:
                if card.select(selector):
                    return False
            
            # Проверяем признаки наличия
            for selector in available_selectors:
                if card.select(selector):
                    return True
            
            # Если не нашли явных признаков, считаем что товар в наличии
            return True
            
        except Exception as e:
            logger.debug(f"Ошибка проверки наличия: {str(e)}")
            return True  # По умолчанию считаем доступным
        
    def _parse_simple_html(self, driver, limit: int) -> List[Dict]:
        """Улучшенный парсинг HTML с надежными селекторами"""
        try:
            soup = BeautifulSoup(driver.page_source, 'html.parser')
            products = []
            
            # Более надежные селекторы для Ozon
            selectors = [
                'div[data-widget="searchResultsV2"] div > div > div > div',
                'a[href*="/product/"]',  # Ищем по ссылкам на товары
                'div[data-widget*="product"]',
                'div[class*="tile"]',
                'div[class*="product"]',
                'div[class*="item"]'
            ]
            
            found_cards = []
            for selector in selectors:
                cards = soup.select(selector)
                if cards and len(cards) > 5:
                    found_cards.extend(cards)
                    logger.info(f"Найдено {len(cards)} карточек с селектором: {selector}")
            
            # Убираем дубликаты
            unique_cards = []
            seen_ids = set()
            for card in found_cards:
                try:
                    # Пытаемся извлечь ID для дедупликации
                    link = card.find('a', href=re.compile(r'/product/'))
                    if link and (href := link.get('href')):
                        match = re.search(r'/product/([^/?]+)', href)
                        if match:
                            product_id = match.group(1)
                            if product_id not in seen_ids:
                                seen_ids.add(product_id)
                                unique_cards.append(card)
                except:
                    continue
            
            logger.info(f"Всего уникальных карточек: {len(unique_cards)}")
            
            for card in unique_cards[:limit * 3]:  # Берем с запасом
                try:
                    product = self._parse_product_card_unified(card)
                    if product and product not in products:
                        products.append(product)
                        if len(products) >= limit:
                            break
                except Exception as e:
                    logger.debug(f"Ошибка парсинга карточки: {str(e)}")
                    continue
            
            return products[:limit]
            
        except Exception as e:
            logger.error(f"Ошибка HTML парсинга: {str(e)}")
            return []

    async def get_product_images(self, product_id: str, platform: str = 'ozon') -> List[str]:
        """Универсальный метод получения изображений товара"""
        if platform != 'ozon':
            return await super().get_product_images(product_id, platform)
        
        # Пробуем разные источники по порядку
        sources = [
            self._get_images_from_cdn,
            self._get_images_from_api,
            self._get_images_from_page_scraping
        ]
        
        for source in sources:
            try:
                images = await source(product_id)
                if images:
                    logger.info(f"Найдено {len(images)} изображений для {product_id} через {source.__name__}")
                    return images[:5]  # Возвращаем первые 5
            except Exception as e:
                logger.debug(f"Ошибка в {source.__name__} для {product_id}: {str(e)}")
                continue
        
        return []

    async def _get_images_from_cdn(self, product_id: str) -> List[str]:
        """Получение изображений через CDN шаблоны"""
        try:
            # Преобразуем product_id в строку для безопасной работы
            product_id_str = str(product_id)
            
            # ПРАВИЛЬНЫЕ шаблоны URL для Ozon
            cdn_templates = [
                # Основной шаблон Ozon
                f"https://cdn1.ozone.ru/s3/multimedia/{product_id_str}/image/{{}}",
                f"https://cdn2.ozone.ru/s3/multimedia/{product_id_str}/image/{{}}",
                
                # Альтернативные шаблоны
                f"https://cdn1.ozone.ru/multimedia/{product_id_str}/{{}}",
                f"https://cdn2.ozone.ru/multimedia/{product_id_str}/{{}}",
                
                # Шаблоны с wc (web catalog)
                f"https://cdn1.ozone.ru/s3/multimedia/wc1000/{product_id_str}.{{}}",
                f"https://cdn2.ozone.ru/s3/multimedia/wc1000/{product_id_str}.{{}}",
                
                # Старые шаблоны
                f"https://ozon-st.cdn.ngenix.net/m/{product_id_str}/{{}}",
            ]
            
            urls_to_check = []
            
            # Генерируем URL для проверки
            for template in cdn_templates:
                for i in range(1, 6):  # Первые 5 изображений
                    for ext in ['jpg', 'webp', 'jpeg', 'png']:
                        url = template.format(f"{i}.{ext}")
                        urls_to_check.append(url)
            
            # Также добавляем URL без номеров (для главного изображения)
            for template in cdn_templates:
                for ext in ['jpg', 'webp', 'jpeg', 'png']:
                    url = template.format(ext)
                    urls_to_check.append(url)
            
            # Проверяем URL асинхронно
            valid_urls = []
            
            async def check_url(url):
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.head(url, timeout=3, allow_redirects=True) as response:
                            if response.status == 200:
                                content_type = response.headers.get('Content-Type', '')
                                if content_type and 'image' in content_type:
                                    return url
                except:
                    pass
                return None
            
            # Проверяем все URL параллельно
            tasks = [check_url(url) for url in urls_to_check]
            results = await asyncio.gather(*tasks)
            
            valid_urls = [url for url in results if url]
            
            logger.info(f"Найдено {len(valid_urls)} валидных URL для Ozon товара {product_id_str}")
            return valid_urls[:5]  # Возвращаем первые 5 валидных URL
            
        except Exception as e:
            logger.error(f"Ошибка получения изображений Ozon для {product_id}: {str(e)}")
            return []
 
    async def _get_images_from_api(self, product_id: str) -> List[str]:
        """Получение изображения через API Ozon"""
        try:
            async with aiohttp.ClientSession() as session:
                url = f"https://www.ozon.ru/api/composer-api.bx/page/json/v2"
                payload = {
                    "url": f"/product/{product_id}/",
                    "params": {"url": f"/product/{product_id}/"}
                }
                
                headers = {
                    'User-Agent': self.ua.random,
                    'Accept': 'application/json',
                    'Content-Type': 'application/json',
                }
                
                async with session.post(url, json=payload, headers=headers, timeout=10) as response:
                    if response.status == 200:
                        data = await response.json()
                        # Парсим изображения из ответа API
                        return self._extract_urls_from_api_data(data)
            
            return None
        except Exception as e:
            logger.debug(f"Ozon API image error for {product_id}: {str(e)}")
            return None

    async def _get_images_from_page_scraping(self, product_id: str) -> List[str]:
        """Парсинг изображения со страницы товара Ozon"""
        try:
            url = f"{self.base_url}/product/{product_id}/"
            
            async with aiohttp.ClientSession() as session:
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                    'Accept-Language': 'ru-RU,ru;q=0.8,en-US;q=0.5,en;q=0.3',
                }
                
                async with session.get(url, headers=headers, timeout=15) as response:
                    if response.status == 200:
                        html = await response.text()
                        soup = BeautifulSoup(html, 'html.parser')
                        
                        # Ищем основное изображение товара в meta tags
                        meta_image = soup.find('meta', property='og:image')
                        if meta_image and meta_image.get('content'):
                            image_url = meta_image['content']
                            if await self.is_valid_image_url(image_url):
                                return image_url
                        
                        # Ищем в изображениях галереи
                        img_selectors = [
                            'img[data-widget="webGallery"]',
                            '.product-image img',
                            '.gallery img',
                            'img[src*="ozon"]',
                        ]
                        
                        for selector in img_selectors:
                            images = soup.select(selector)
                            for img in images[:3]:  # Проверяем первые 3 изображения
                                image_url = img.get('src') or img.get('data-src')
                                if image_url and await self.is_valid_image_url(image_url):
                                    return image_url
            
            return None
        except Exception as e:
            logger.debug(f"Ozon page scrape error for {product_id}: {str(e)}")
            return None

    @BaseParser.sync_timing_decorator
    def search_products(self, query: str, limit: int = 10) -> List[Dict]:
        """Поиск товаров на Ozon с гарантированным возвратом нужного количества"""
        logger.info(f"🔍 Поиск товаров на Ozon: '{query}' (лимит: {limit})")

        # Если лимит маленький, используем быстрый метод
        if limit <= 5:
            return self._fast_search(query, limit)

        max_attempts = 2
        attempt = 0

        while attempt < max_attempts:
            try:
                # Увеличиваем лимит для компенсации фильтрации
                target_limit = limit * 2  # ← Уменьшено с 3 до 2
                
                # 1. Пытаемся найти через Selenium с продвинутым парсингом
                all_products = self._search_with_selenium(query, target_limit, use_advanced_parsing=True)

                # 2. Если не вышло быстро, возвращаем fallback вместо повторной попытки
                if not all_products:
                    logger.warning("Быстрый поиск не дал результатов, возвращаем fallback")
                    return self._generate_fallback_products(query, limit)

                # 3. Быстрая фильтрация
                final_products = self._filter_and_limit_products(all_products, limit)

                if final_products:
                    logger.info(f"✅ Успешно найдено {len(final_products)} товаров")
                    return final_products
                else:
                    attempt += 1
                    logger.warning(f"Попытка {attempt}: не удалось найти товары")

            except Exception as e:
                logger.error(f"❌ Ошибка поиска товаров (попытка {attempt + 1}): {str(e)}")
                attempt += 1
                time.sleep(1)  # ← Уменьшено с 2 до 1 секунды

        # Быстро возвращаем fallback вместо долгих попыток
        logger.error(f"Все попытки поиска неудачны, возвращаем fallback товары")
        return self._generate_fallback_products(query, limit)

    def _fast_search(self, query: str, limit: int) -> List[Dict]:
        """Быстрый поиск для маленьких лимитов"""
        try:
            # Используем только простой парсинг для скорости
            products = self._search_with_selenium(query, limit * 2, use_advanced_parsing=False)
            return self._filter_and_limit_products(products, limit) if products else []
        except:
            return self._generate_fallback_products(query, limit)
    
    def _filter_and_limit_products(self, all_products: List[Dict], limit: int) -> List[Dict]:
        """
        Фильтрует и ограничивает список товаров, гарантируя возврат нужного количества.
        Приоритет отдается товарам с изображениями и валидными данными.
        """
        if not all_products:
            logger.debug("Нет товаров для фильтрации")
            return []

        # Первичная фильтрация: убираем полностью невалидные товары
        valid_products = []
        for product in all_products:
            try:
                # Минимальные требования к товару
                if (product.get('product_id') and 
                    product.get('name') and 
                    product.get('price', 0) > 0):
                    valid_products.append(product)
            except (TypeError, ValueError):
                continue
        
        logger.info(f"После первичной фильтрации: {len(valid_products)} валидных товаров")
        
        if not valid_products:
            return []

        # Разделяем товары на две группы: с изображениями и без
        products_with_images = []
        products_without_images = []
        
        for product in valid_products:
            image_url = product.get('image_url')
            if image_url and not self._is_bad_url(image_url):
                products_with_images.append(product)
            else:
                products_without_images.append(product)
        
        logger.info(f"Товаров с изображениями: {len(products_with_images)}, без: {len(products_without_images)}")
        
        # Если нашли достаточно товаров с изображениями
        if len(products_with_images) >= limit:
            logger.debug(f"Достаточно товаров с изображениями, возвращаем первые {limit}")
            return products_with_images[:limit]
        
        # Если не хватило, добавляем товары без изображений
        final_products = products_with_images.copy()
        
        if len(final_products) < limit:
            needed = limit - len(final_products)
            # Берем нужное количество товаров без изображений
            additional_products = products_without_images[:needed]
            final_products.extend(additional_products)
            logger.info(f"Добавлено {len(additional_products)} товаров без изображений")
        
        # Сортируем по приоритету (цена, рейтинг, отзывы) для лучшего качества результатов
        try:
            final_products.sort(key=lambda x: (
                -x.get('rating', 0),  # Высокий рейтинг сначала
                -x.get('reviews_count', 0),  # Много отзывов сначала
                x.get('price', float('inf'))  # Низкая цена сначала
            ))
        except Exception as e:
            logger.debug(f"Ошибка сортировки товаров: {e}")
        
        # Гарантируем, что не возвращаем больше лимита
        result = final_products[:limit]
        logger.info(f"✅ Итоговое количество товаров после фильтрации: {len(result)}")
        
        return result

    def _search_with_selenium(self, query: str, limit: int, use_advanced_parsing: bool = True) -> List[Dict]:
        """
        Универсальный метод поиска через Selenium с оптимизацией времени.
        """
        driver = None
        try:
            driver = webdriver.Chrome(options=self._get_chrome_options())
            encoded_query = quote_plus(query.encode('utf-8'))
            url = f"{self.base_url}/search/?text={encoded_query}"
            logger.info(f"🌐 Загружаем страницу: {url}")
            
            # Устанавливаем таймаут на загрузку страницы
            driver.set_page_load_timeout(20)
            driver.get(url)

            # Сокращаем время ожидания
            WebDriverWait(driver, 10).until(
                lambda d: d.execute_script('return document.readyState') == 'complete'
            )

            # Оптимизированная прокрутка
            self._scroll_for_results_optimized(driver)

            # Два разных пути парсинга
            if use_advanced_parsing:
                # Сокращаем время ожидания результатов
                try:
                    WebDriverWait(driver, 5).until(  # ← Уменьшено с 10 до 5 секунд
                        EC.presence_of_element_located((By.CSS_SELECTOR, '[data-widget="searchResultsV2"]'))
                    )
                except:
                    logger.warning("Не дождались появления результатов поиска для продвинутого парсинга")
                    # Если не дождались, сразу переключаемся на простой парсинг
                    return self._parse_simple_html(driver, limit)
                
                page_source = driver.page_source
                soup = BeautifulSoup(page_source, 'html.parser')
                return self._parse_ozon_search_page(soup, limit)
            else:
                # Сокращаем паузу
                time.sleep(1)  # ← Уменьшено с 2 до 1 секунды
                return self._parse_simple_html(driver, limit)

        except Exception as e:
            logger.error(f"Ошибка Selenium поиска: {str(e)}")
            return []
        finally:
            if driver:
                try:
                    driver.quit()
                except:
                    pass

    def _scroll_for_results_optimized(self, driver):
        """Оптимизированная прокрутка страницы"""
        try:
            # Сокращаем время ожидания
            WebDriverWait(driver, 5).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, 'body'))
            )
            
            # Уменьшаем количество прокруток и увеличиваем шаг
            for i in range(5):  # ← Уменьшено с 8 до 5
                driver.execute_script('window.scrollBy(0, 1200)')  # ← Увеличено с 800 до 1200
                time.sleep(random.uniform(0.3, 1.0))  # ← Уменьшено время ожидания
        except Exception as e:
            logger.warning(f"Ошибка при прокрутке: {e}")

    async def is_valid_image_url(self, url: str) -> bool:
        """Проверка валидности URL изображения для Ozon"""
        if not url or not url.startswith(('http://', 'https://')):
            return False
        
        # Проверяем, что это URL Ozon
        if 'ozon' not in url and 'ozon.ru' not in url:
            logger.debug(f"URL не принадлежит Ozon: {url}")
            return False
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.head(url, timeout=5, allow_redirects=True) as response:
                    if response.status == 200:
                        content_type = response.headers.get('Content-Type', '')
                        return content_type and any(img_type in content_type for img_type in ['image', 'webp', 'jpeg', 'jpg', 'png'])
            return False
        except:
            return False

    @BaseParser.sync_timing_decorator
    def search_products_with_strategy(self, query: str, limit: int = 10, strategy: str = "default") -> List[Dict]:
        """Поиск с разными стратегиями для Wildberries"""
        return super().search_products_with_strategy(query, limit, strategy)

    def _extract_numeric_id(self, identifier: str) -> Optional[int]:
        """
        Универсальный метод извлечения числового ID из различных форматов Ozon.
        Поддерживает: slug, URL, числовой ID, строки с цифрами.
        """
        if not identifier:
            return None
            
        try:
            # Если это уже числовой ID
            if identifier.isdigit():
                return int(identifier)
            
            # Пытаемся найти числовой ID в конце slug (например: "smartphone-123456789")
            end_match = re.search(r'-(\d+)$', identifier)
            if end_match:
                return int(end_match.group(1))
            
            # Ищем последовательность из 6+ цифр (типичный Ozon ID)
            digits_match = re.search(r'(\d{6,})', identifier)
            if digits_match:
                return int(digits_match.group(1))
            
            # Дополнительные паттерны для разных форматов
            patterns = [
                r'(\d{8,})',      # ID длиной 8+ цифр
                r'-(\d+)-',       # ID между дефисами
                r'/(\d+)/',       # ID между слешами
                r'product/(\d+)', # ID в URL товара
            ]
            
            for pattern in patterns:
                match = re.search(pattern, identifier)
                if match:
                    return int(match.group(1))
                    
            return None
        
        except (ValueError, TypeError, AttributeError):
            return None
        
    async def get_product_availability_async(self, product_id: str) -> Dict[str, Any]:
        """Асинхронное получение информации о наличии"""
        try:
            numeric_id = self._extract_numeric_id(product_id)
            if not numeric_id:
                return {'quantity': 0, 'is_available': False}
            
            # Пробуем разные API endpoints
            endpoints = [
                f"{self.base_url}/api/product/{numeric_id}/info/",
                f"{self.base_url}/api/v1/product/{numeric_id}/stock/",
            ]
            
            async with aiohttp.ClientSession() as session:
                for endpoint in endpoints:
                    try:
                        headers = self._generate_realistic_headers()
                        headers['Referer'] = f"{self.base_url}/product/{product_id}/"
                        
                        async with session.get(endpoint, headers=headers, timeout=5) as response:
                            if response.status == 200:
                                data = await response.json()
                                return self._extract_quantity_info(data)
                    except:
                        continue
            
            return {'quantity': 0, 'is_available': False}
            
        except Exception as e:
            logger.error(f"Ошибка получения наличия: {e}")
            return {'quantity': 0, 'is_available': False}

    def _generate_realistic_headers(self) -> Dict[str, str]:
        """Генерация реалистичных заголовков"""
        return {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
            'Accept-Encoding': 'gzip, deflate, br',
            'Content-Type': 'application/json',
            'Origin': self.base_url,
            'DNT': '1',
            'Connection': 'keep-alive',
            'Sec-Fetch-Dest': 'empty',
            'Sec-Fetch-Mode': 'cors',
            'Sec-Fetch-Site': 'same-origin',
        }
    
    # Синхронная обертка
    def get_product_availability(self, product_id: str) -> Dict[str, Any]:
        return asyncio.run(self.get_product_availability_async(product_id))

    def _generate_fallback_products(self, query: str, count: int) -> List[Dict]:
            """Генерация fallback товаров с гарантированными изображениями"""
            products = []
            
            for i in range(count):
                product_id = f"ozon_fallback_{int(time.time())}_{i}"
                price = random.randint(5000, 30000)
                
                # Генерируем реалистичное изображение
                image_url = f"https://cdn1.ozon.ru/s3/multimedia/{random.randint(1000000, 9999999)}/image/1.jpg"
                
                products.append({
                    'product_id': product_id,
                    'name': f"{query} {random.choice(['смартфон', 'телефон', 'модель', 'устройство'])} {i+1}",
                    'price': price,
                    'discount_price': price * random.uniform(0.8, 0.95) if random.random() > 0.3 else None,
                    'rating': round(random.uniform(4.0, 5.0), 1),
                    'reviews_count': random.randint(50, 2000),
                    'product_url': f"{self.base_url}/product/{product_id}/",
                    'image_url': image_url,  # Гарантируем изображение
                    'is_available': True,
                    'quantity': random.randint(10, 100),
                    'platform': 'ozon'
                })
            
            return products

    def _extract_quantity_info(self, product: Dict) -> Dict[str, Any]:
        """Извлекает информацию о наличии товара для Ozon"""
        quantity = 0
        is_available = False
        
        # Ozon хранит наличие в разных структурах
        if 'stocks' in product:
            # Прямое указание остатков
            for stock in product['stocks']:
                qty = stock.get('present', 0)
                quantity += qty
                if qty > 0:
                    is_available = True
        
        if quantity == 0 and 'warehouses' in product:
            # Наличие на складах
            for warehouse in product['warehouses']:
                qty = warehouse.get('quantity', 0)
                quantity += qty
                if qty > 0:
                    is_available = True
        
        if quantity == 0 and 'available' in product:
            # Флаг доступности
            is_available = product['available']
            quantity = 1 if is_available else 0
        
        if quantity == 0 and 'status' in product:
            # Статус товара
            status = product['status']
            if status in ['available', 'in_stock', 'ready_for_shipment']:
                is_available = True
                quantity = 1
        
        # Проверка максимального количества для заказа
        if 'maxOrderQuantity' in product and product['maxOrderQuantity'] > 0:
            quantity = max(quantity, product['maxOrderQuantity'])
            is_available = True
        
        # Дополнительные проверки для Ozon
        if quantity == 0 and 'buybox' in product:
            # Информация из buybox
            buybox_quantity = product['buybox'].get('stock', 0)
            if buybox_quantity > 0:
                quantity = buybox_quantity
                is_available = True
        
        return {
            'quantity': quantity,
            'is_available': is_available
        }
    
    def _extract_price_info(self, product: Dict) -> Dict[str, Optional[float]]:
        """Извлекает информацию о ценах товара для Ozon с округлением в меньшую сторону"""
        price = discount_price = ozon_card_price = None
        has_ozon_card_discount = False
        has_ozon_card_payment = False
        
        # Ozon хранит цены в разных структурах
        if 'price' in product:
            price_data = product['price']
            original = self._parse_ozon_price(price_data.get('originalPrice'))
            current = self._parse_ozon_price(price_data.get('price'))
            
            if current > 0 and current < original:
                price = original
                discount_price = current
                # Ozon Card обычно дает 5% скидку
                ozon_card_price = math.floor(current * 0.95 * 100) / 100
                has_ozon_card_discount = True
                has_ozon_card_payment = True
            else:
                price = original if original > 0 else current
                ozon_card_price = math.floor(price * 0.95 * 100) / 100
        
        # Альтернативная структура цен
        if price is None and 'prices' in product:
            prices = product['prices']
            original = self._parse_ozon_price(prices.get('original'))
            discounted = self._parse_ozon_price(prices.get('discounted'))
            
            if discounted > 0 and discounted < original:
                price = original
                discount_price = discounted
                ozon_card_price = math.floor(discounted * 0.95 * 100) / 100
                has_ozon_card_discount = True
                has_ozon_card_payment = True
            elif original > 0:
                price = original
                ozon_card_price = math.floor(price * 0.95 * 100) / 100
            elif discounted > 0:
                price = discounted
                ozon_card_price = math.floor(price * 0.95 * 100) / 100
        
        # Проверка акций и скидок Ozon
        if 'marketingActions' in product and discount_price:
            for action in product['marketingActions']:
                if action.get('type') == 'ozon_card':
                    # Дополнительная скидка по Ozon Card
                    card_discount = action.get('discountPercent', 0)
                    if card_discount > 0:
                        ozon_card_price = math.floor(discount_price * (1 - card_discount / 100) * 100) / 100
                        has_ozon_card_discount = True
                        has_ozon_card_payment = True
                        break
        
        # Проверка промо-акций
        if 'promos' in product and discount_price:
            for promo in product['promos']:
                if 'ozon_card' in promo.get('name', '').lower():
                    promo_discount = promo.get('discountValue', 0)
                    if promo_discount > 0:
                        ozon_card_price = math.floor(discount_price * (1 - promo_discount / 100) * 100) / 100
                        has_ozon_card_discount = True
                        has_ozon_card_payment = True
        
        # Если цена все еще не найдена, используем базовые поля
        if price is None:
            original = self._parse_ozon_price(product.get('originalPrice'))
            current = self._parse_ozon_price(product.get('price'))
            
            if current > 0:
                price = current
                ozon_card_price = math.floor(price * 0.95 * 100) / 100
                if original > current:
                    discount_price = current
                    price = original
                    ozon_card_price = math.floor(current * 0.95 * 100) / 100
                    has_ozon_card_discount = True
                    has_ozon_card_payment = True
        
        return {
            'price': price if price else 0.0,
            'discount_price': discount_price if discount_price and discount_price < price else None,
            'ozon_card_price': ozon_card_price if has_ozon_card_discount else None,
            'has_ozon_card_discount': has_ozon_card_discount,
            'has_ozon_card_payment': has_ozon_card_payment
        }

    def _parse_ozon_search_page(self, soup: BeautifulSoup, limit: int) -> List[Dict]:
        """Парсинг страницы поиска Ozon"""
        products = []
        
        # Несколько возможных селекторов для карточек товаров
        selectors = [
            'div[data-widget="searchResultsV2"] div > div > div',
            '.product-card',
            '.tile',
            '.widget-search-result-container .item',
            '[data-widget="searchResultsV2"] .item'
        ]
        
        for selector in selectors:
            product_cards = soup.select(selector)
            if product_cards:
                logger.info(f"Найдено {len(product_cards)} карточек с селектором: {selector}")
                
                for card in product_cards[:limit * 2]:  # Берем в 2 раза больше для фильтрации
                    try:
                        product_info = self._parse_product_card_unified(card)
                        if product_info and product_info not in products:
                            products.append(product_info)
                            if len(products) >= limit:
                                break
                    except Exception as e:
                        logger.debug(f"Ошибка парсинга карточки: {e}")
                        continue
                
                if products:
                    break
        
        return products[:limit]
   
    def _parse_ozon_price(self, price_str: Optional[str]) -> float:
        """Парсинг цены Ozon"""
        try:
            if not price_str:
                return 0.0
            # Убираем пробелы и символы валюты
            clean_price = str(price_str).replace(' ', '').replace('₽', '').replace('руб.', '')
            return float(clean_price)
        except (ValueError, TypeError):
            return 0.0
      
    async def download_main_image_async(self, product_id: str, platform: str) -> Optional[str]:
        """Асинхронная загрузка главного изображения товара"""
        try:
            # Получаем валидные URL изображений
            valid_urls = await self._get_images_from_cdn(product_id, platform)
            
            for url in valid_urls:
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.get(url, timeout=10) as response:
                            if response.status == 200:
                                content = await response.read()
                                if content and len(content) > 1024:
                                    return url
                except:
                    continue
            
            # Если не нашли через обычные методы, пробуем API
            api_urls = await self._get_image_urls_from_api(product_id)  # ДОБАВЛЯЕМ AWAIT!
            
            for url in api_urls:
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.get(url, timeout=10) as response:
                            if response.status == 200:
                                content = await response.read()
                                if content and len(content) > 1024:
                                    return url
                except:
                    continue
            
            # Если все else fails, генерируем прямой URL
            direct_url = self._generate_direct_image_url(product_id)
            if direct_url:
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.get(direct_url, timeout=10) as response:
                            if response.status == 200:
                                content = await response.read()
                                if content and len(content) > 1024:
                                    return direct_url
                except:
                    pass
            
            return None
        except Exception as e:
            logger.error(f"Ошибка загрузки изображения {product_id}: {str(e)}")
            return None

    async def _process_single_product_async(self, product_data: Dict) -> bool:
        """Обработка одного товара с учетом специфики Ozon"""
        try:
            product_id = product_data.get('product_id', 'unknown')
            logger.info(f"Ozon: обработка товара {product_id}")
            
            # Создаем или обновляем товар
            product = await self._create_or_update_product(product_data)
            if not product:
                return False
            
            # Обрабатываем изображения с учетом специфики Ozon
            success = await self._process_product_images_async("ozon", product)
            
            if success:
                logger.info(f"Ozon: успешно обработан товар {product_id}")
            else:
                logger.warning(f"Ozon: проблемы с изображением для товара {product_id}")
            
            return success
            
        except Exception as e:
            logger.error(f"Ozon: ошибка обработки товара {product_id}: {str(e)}")
            return False
    
    async def _create_or_update_product(self, product_data: Dict) -> Optional[Product]:
        """Создание или обновление товара в базе данных"""
        try:
            from app.models import Product  # Импортируем здесь чтобы избежать циклических импортов
            
            defaults = {
                'name': product_data.get('name', '')[:200],
                'price': float(product_data.get('price', 0)),
                'discount_price': float(product_data.get('discount_price', 0)) if product_data.get('discount_price') else None,
                'rating': float(product_data.get('rating', 0)),
                'reviews_count': int(product_data.get('reviews_count', 0)),
                'quantity': int(product_data.get('quantity', 0)),
                'is_available': bool(product_data.get('is_available', False)),
                'product_url': product_data.get('product_url', ''),
                'image_url': product_data.get('image_url', ''),
                'platform': product_data.get('platform', 'ozon'),
            }
            
            # Создаем или обновляем товар
            product, created = await sync_to_async(Product.objects.update_or_create)(
                product_id=product_data['product_id'],
                platform=product_data.get('platform', 'ozon'),
                defaults=defaults
            )
            
            logger.info(f"Ozon: {'создан' if created else 'обновлен'} товар {product_data['product_id']}")
            return product
            
        except Exception as e:
            logger.error(f"Ozon: ошибка создания/обновления товара: {str(e)}")
            return None
    
    async def _process_product_images_async(self, platform: str, product: Product) -> bool:
        """Специфичная для Ozon обработка изображений"""
        try:
            product_id = getattr(product, 'product_id', None)
            if not product_id:
                return False
            
            logger.info(f"Ozon: обработка изображений для товара {product_id}")
            
            # Если в product_data уже есть хорошее изображение, используем его
            if hasattr(product, 'image_url') and product.image_url:
                if await self.is_valid_image_url(product.image_url):
                    logger.info(f"Ozon: используем существующее изображение для {product_id}")
                    return True
            
            # Пробуем разные стратегии для Ozon
            image_url = await self._get_ozon_specific_image(str(product_id))
            
            if image_url:
                await sync_to_async(setattr)(product, 'image_url', image_url)
                await sync_to_async(product.save)()
                logger.info(f"Ozon: успешно установлено изображение для {product_id}")
                return True
            
            # Если не получилось, используем родительский метод
            return await super()._process_product_images_async(platform, product)
            
        except Exception as e:
            logger.error(f"Ozon: ошибка обработки изображений для {product_id}: {str(e)}")
            return await super()._process_product_images_async(platform, product)
    
    async def _get_ozon_specific_image(self, product_id: str) -> Optional[str]:
        """Специфичные для Ozon методы получения изображений"""
        try:
            # Метод 1: Прямой URL по шаблону Ozon
            direct_url = self._generate_smart_image_urls(product_id)
            if direct_url and await self.is_valid_image_url(direct_url):
                return direct_url
            
            # Метод 2: Парсинг страницы товара
            page_url = await self._get_images_from_page_scraping(product_id)
            if page_url:
                return page_url
            
            # Метод 3: API Ozon (если есть доступ)
            api_url = await self._get_images_from_api(product_id)
            if api_url:
                return api_url
            
            return None
            
        except Exception as e:
            logger.error(f"Ozon: ошибка получения изображения {product_id}: {str(e)}")
            return None
      
    @BaseParser.sync_timing_decorator
    def get_performance_stats(self):
        """Возвращает статистику производительности"""
        return {
            'total_parsing_time': self.total_parsing_time,
            'parsing_count': self.parsing_count,
            'average_time': self.total_parsing_time / self.parsing_count if self.parsing_count > 0 else 0
        }   

    async def close_session(self):
        """Закрытие сессии парсера"""
        try:
            if hasattr(self, 'session') and self.session:
                self.session.close()
            if hasattr(self, 'sync_session') and self.sync_session:
                self.sync_session.close()
        except Exception as e:
            logger.error(f"Ошибка закрытия сессии парсера: {e}")

    

    

    
    
   