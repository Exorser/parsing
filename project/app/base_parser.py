import requests
import logging
from typing import List, Dict, Tuple, Optional, Any, Union
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
from django.db.models import Q
from asgiref.sync import sync_to_async
from abc import ABC, abstractmethod
import json
from bs4 import BeautifulSoup
import re
import urllib
import random
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, WebDriverException
from selenium_stealth import stealth
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.service import Service
from concurrent.futures import ThreadPoolExecutor


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
        """Проверяет, является ли URL плохим"""
        if not url:
            return True
            
        if isinstance(url, str) and url.strip() == '':
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
        super().__init__('WB')
        self.base_url = "https://www.wildberries.ru"
        self.search_url = "https://search.wb.ru/exactmatch/ru/common/v4/search"
        
        self.session.headers.update({
            'Referer': 'https://www.wildberries.ru/'
        })

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
    
# class OzonParser(BaseParser):
    # """Парсер для Ozon с интегрированными методами обхода защиты"""
    
    # def __init__(self):
    #     super().__init__('OZ')
    #     self.base_url = "https://www.ozon.ru"
    #     self.product_url = "https://www.ozon.ru/product/"
        
    #     # Настройки из работающего парсера
    #     self.session = requests.Session()
    #     self._setup_session()
    #     self.workers = 3  # Количество воркеров
        
    #     # API endpoints из работающего парсера
    #     self.api_endpoints = [
    #         "https://www.ozon.ru/api/composer-api.bx/_action/search",
    #         "https://www.ozon.ru/api/entrypoint-api.bx/page/json/v2",
    #         "https://mobile-api.ozon.ru/v1/search",
    #     ]

    # def _setup_session(self):
    #     """Настройка сессии с обходом защиты"""
    #     self.session.headers.update({
    #         'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    #         'Accept': 'application/json, text/plain, */*',
    #         'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
    #         'Accept-Encoding': 'gzip, deflate, br',
    #         'Connection': 'keep-alive',
    #         'Sec-Fetch-Dest': 'empty',
    #         'Sec-Fetch-Mode': 'cors',
    #         'Sec-Fetch-Site': 'same-origin',
    #         'Referer': 'https://www.ozon.ru/',
    #         'Origin': 'https://www.ozon.ru',
    #     })

    # def _rotate_headers(self):
    #     """Ротация заголовков для обхода защиты"""
    #     user_agents = [
    #         'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    #         'Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15',
    #         'Mozilla/5.0 (Linux; Android 13; SM-S901B) AppleWebKit/537.36',
    #     ]
        
    #     self.session.headers['User-Agent'] = random.choice(user_agents)
    #     self.session.headers['X-Requested-With'] = 'XMLHttpRequest'
    #     self.session.headers['Cache-Control'] = 'no-cache'
    #     self.session.headers['Pragma'] = 'no-cache'

    # def _make_authenticated_request(self, url, payload=None, timeout=15):
    #     """Создание аутентифицированного запроса с обходом защиты"""
    #     try:
    #         self._rotate_headers()
            
    #         if payload:
    #             response = self.session.post(
    #                 url, 
    #                 json=payload, 
    #                 timeout=timeout,
    #                 # proxies=self._get_proxy()  # Добавить если есть прокси
    #             )
    #         else:
    #             response = self.session.get(
    #                 url,
    #                 timeout=timeout,
    #                 # proxies=self._get_proxy()
    #             )
            
    #         if response.status_code == 200:
    #             return response.json()
    #         elif response.status_code in [403, 429]:
    #             logger.warning(f"Блокировка запроса: {response.status_code}")
    #             self._handle_blockage()
    #             return None
                
    #     except Exception as e:
    #         logger.error(f"Ошибка запроса: {e}")
    #         return None
        
    #     return None

    # def _handle_blockage(self):
    #     """Обработка блокировки"""
    #     logger.warning("Обнаружена блокировка, применяем меры обхода...")
    #     time.sleep(random.uniform(2, 5))
    #     self._rotate_headers()
    #     # Здесь можно добавить смену прокси, куков и т.д.

    # @BaseParser.sync_timing_decorator
    # def search_products(self, query: str, limit: int = 10) -> List[Dict]:
    #     """Поиск товаров с использованием воркеров"""
    #     try:
    #         # Пробуем разные стратегии
    #         strategies = [
    #             self._search_via_api_workers,
    #             self._search_via_mobile_api,
    #             self._search_via_direct_api,
    #         ]
            
    #         for strategy in strategies:
    #             try:
    #                 logger.info(f"Пробуем стратегию: {strategy.__name__}")
    #                 products = strategy(query, limit)
    #                 if products:
    #                     logger.info(f"Стратегия {strategy.__name__} нашла {len(products)} товаров")
    #                     return products[:limit]
    #                 time.sleep(random.uniform(1, 3))
    #             except Exception as e:
    #                 logger.warning(f"Стратегия {strategy.__name__} не сработала: {e}")
    #                 continue
            
    #         logger.warning("Все стратегии не сработали, используем mock данные")
    #         return self._get_mock_products(query, limit)
            
    #     except Exception as e:
    #         logger.error(f"Ошибка в поиске: {e}")
    #         return self._get_mock_products(query, limit)

    # def _search_via_api_workers(self, query: str, limit: int) -> List[Dict]:
    #     """Поиск через API с использованием воркеров"""
    #     try:
    #         # Используем многопоточность как в том парсере
    #         results = []
            
    #         with ThreadPoolExecutor(max_workers=self.workers) as executor:
    #             futures = []
                
    #             for offset in range(0, limit, 10):  # Пагинация по 10 товаров
    #                 future = executor.submit(
    #                     self._api_worker,
    #                     query,
    #                     offset,
    #                     min(10, limit - offset)
    #                 )
    #                 futures.append(future)
                
    #             for future in futures:
    #                 try:
    #                     products = future.result(timeout=20)
    #                     if products:
    #                         results.extend(products)
    #                     if len(results) >= limit:
    #                         break
    #                 except Exception as e:
    #                     logger.debug(f"Ошибка воркера: {e}")
    #                     continue
            
    #         return results[:limit]
            
    #     except Exception as e:
    #         logger.error(f"Ошибка воркеров: {e}")
    #         return []
        
    # def _api_worker(self, query: str, offset: int, limit: int) -> List[Dict]:
    #     """Воркер для API запросов"""
    #     try:
    #         payload = {
    #             "query": query,
    #             "listing": {
    #                 "limit": limit,
    #                 "offset": offset
    #             },
    #             "filters": [],
    #             "sort": {"type": "rating", "direction": "desc"}
    #         }
            
    #         data = self._make_authenticated_request(
    #             self.api_endpoints[0],
    #             payload
    #         )
            
    #         if data:
    #             return self._extract_products_from_api(data)
                
    #     except Exception as e:
    #         logger.debug(f"Ошибка API воркера: {e}")
        
    #     return []

    # def _search_via_mobile_api(self, query: str, limit: int) -> List[Dict]:
    #     """Поиск через мобильное API"""
    #     try:
    #         url = f"https://mobile-api.ozon.ru/v1/search?text={urllib.parse.quote(query)}&limit={limit}"
            
    #         data = self._make_authenticated_request(url)
    #         if data:
    #             return self._extract_products_from_api(data)
                
    #     except Exception as e:
    #         logger.debug(f"Ошибка мобильного API: {e}")
        
    #     return []

    # def _search_via_direct_api(self, query: str, limit: int) -> List[Dict]:
    #     """Прямые API запросы"""
    #     try:
    #         for endpoint in self.api_endpoints:
    #             try:
    #                 if endpoint.endswith('/search'):
    #                     payload = {"query": query, "limit": limit}
    #                     data = self._make_authenticated_request(endpoint, payload)
    #                 else:
    #                     url = f"{endpoint}?text={urllib.parse.quote(query)}&limit={limit}"
    #                     data = self._make_authenticated_request(url)
                    
    #                 if data:
    #                     products = self._extract_products_from_api(data)
    #                     if products:
    #                         return products
                            
    #             except Exception as e:
    #                 logger.debug(f"Ошибка endpoint {endpoint}: {e}")
    #                 continue
                    
    #     except Exception as e:
    #         logger.debug(f"Ошибка direct API: {e}")
        
    #     return []

    # def _extract_products_from_api(self, data: Dict) -> List[Dict]:
    #     """Извлечение товаров из API ответа (адаптировать под его формат)"""
    #     products = []
        
    #     try:
    #         # Анализируйте структуру ответа из его парсера
    #         possible_paths = [
    #             ['data', 'products'],
    #             ['products'],
    #             ['items'],
    #             ['result', 'items'],
    #             ['data', 'items'],
    #             ['searchResults', 'products'],
    #         ]
            
    #         for path in possible_paths:
    #             try:
    #                 current = data
    #                 for key in path:
    #                     current = current[key]
    #                 if isinstance(current, list):
    #                     products = current
    #                     break
    #             except (KeyError, TypeError):
    #                 continue
            
    #         # Парсим продукты
    #         parsed_products = []
    #         for product in products:
    #             parsed = self._parse_api_product(product)
    #             if parsed:
    #                 parsed_products.append(parsed)
            
    #         return parsed_products
            
    #     except Exception as e:
    #         logger.error(f"Ошибка извлечения товаров: {e}")
    #         return []
    
    # def _get_mock_products(self, query: str, limit: int) -> List[Dict]:
    #     """Возвращает mock данные"""
    #     mock_products = []
    #     for i in range(limit):
    #         mock_products.append({
    #             'product_id': f"ozon_mock_{i}_{hash(query)}",
    #             'name': f"{query} (пример {i+1})",
    #             'price': 1000 + i * 100,
    #             'discount_price': 900 + i * 100 if i % 2 == 0 else None,
    #             'rating': 4.0 + random.random(),
    #             'reviews_count': random.randint(10, 1000),
    #             'product_url': f"https://www.ozon.ru/product/mock-{i}/",
    #             'image_url': f"https://via.placeholder.com/300x300?text=Ozon+{query}+{i+1}",
    #             'quantity': random.randint(1, 100),
    #             'is_available': True
    #         })
    #     return mock_products

    # @lru_cache(maxsize=1000)
    # @BaseParser.sync_timing_decorator
    # def _generate_smart_image_urls(self, product_id: Union[int, str]) -> List[str]:
    #     """Генерация URL изображений для Ozon"""
    #     try:
    #         if isinstance(product_id, str) and product_id.startswith('ozon_mock_'):
    #             return [f"https://via.placeholder.com/300x300?text=Ozon+Product"]
            
    #         if isinstance(product_id, str):
    #             numeric_id = ''.join(filter(str.isdigit, product_id))
    #             if numeric_id:
    #                 product_id = int(numeric_id)
    #             else:
    #                 return []
            
    #         product_id = int(product_id)
            
    #         urls = [
    #             f"https://ozon-st.cdn.ngenix.net/multimedia/{product_id}/image.jpg",
    #             f"https://cdn1.ozone.ru/multimedia/{product_id}/image.jpg",
    #             f"https://ozon-st.cdn.ngenix.net/multimedia/c500/{product_id}.jpg",
    #             f"https://www.ozon.ru/multimedia/{product_id}/image.jpg",
    #         ]
            
    #         return list(dict.fromkeys(urls))
            
    #     except Exception as e:
    #         logger.error(f"Ошибка генерации URL: {e}")
    #         return []

    # def _extract_quantity_info(self, product: Dict) -> Dict[str, Any]:
    #     """Извлекает информацию о наличии товара"""
    #     try:
    #         quantity = product.get('quantity', 0)
    #         is_available = product.get('isAvailable', False) or quantity > 0
            
    #         if 'stock' in product:
    #             stock = product['stock']
    #             if isinstance(stock, dict):
    #                 quantity = stock.get('count', quantity)
    #                 is_available = stock.get('available', is_available)
                
    #         return {
    #             'quantity': quantity,
    #             'is_available': is_available
    #         }
            
    #     except Exception as e:
    #         logger.error(f"Ошибка извлечения наличия: {e}")
    #         return {'quantity': 0, 'is_available': False}

    # def _extract_price_info(self, product: Dict) -> Dict[str, Optional[float]]:
    #     """Извлекает информацию о ценах товара"""
    #     try:
    #         price_data = product.get('price', {})
            
    #         if isinstance(price_data, str):
    #             try:
    #                 price_data = json.loads(price_data)
    #             except:
    #                 price_data = {}
            
    #         price = self._clean_price(price_data.get('price', product.get('price', 0)))
    #         original_price = self._clean_price(
    #             price_data.get('originalPrice', price_data.get('discountPrice', price))
    #         )
            
    #         ozon_card_price = self._clean_price(
    #             price_data.get('ozonCardPrice', product.get('ozonCardPrice'))
    #         )
            
    #         has_discount = original_price < price
    #         has_ozon_card = ozon_card_price is not None and ozon_card_price < (original_price if has_discount else price)
            
    #         return {
    #             'price': price,
    #             'discount_price': original_price if has_discount else None,
    #             'ozon_card_price': ozon_card_price if has_ozon_card else None,
    #             'has_ozon_card_discount': has_ozon_card,
    #             'has_ozon_card_payment': True
    #         }
            
    #     except Exception as e:
    #         logger.error(f"Ошибка извлечения цен: {e}")
    #         return {
    #             'price': 0.0,
    #             'discount_price': None,
    #             'ozon_card_price': None,
    #             'has_ozon_card_discount': False,
    #             'has_ozon_card_payment': False
    #         }
    
    # def _clean_price(self, price_value) -> float:
    #     """Очистка и конвертация цены"""
    #     if isinstance(price_value, (int, float)):
    #         return float(price_value)
        
    #     if isinstance(price_value, str):
    #         cleaned = re.sub(r'[^\d.]', '', price_value)
    #         try:
    #             return float(cleaned) if cleaned else 0.0
    #         except ValueError:
    #             return 0.0
        
    #     return 0.0

    # @BaseParser.sync_timing_decorator
    # def _get_image_urls_from_api(self, product_id: int) -> List[str]:
    #     """Получение ТОЛЬКО правильных изображений через API Ozon"""
    #     try:
    #         cache_key = f"ozon_api_{product_id}"
    #         cached = cache.get(cache_key)
    #         if cached:
    #             return cached
                
    #         response = requests.post(
    #             f"https://www.ozon.ru/api/entrypoint-api.bx/page/json/v2",
    #             headers={
    #                 'User-Agent': self.ua.random,
    #                 'Content-Type': 'application/json',
    #                 'Origin': 'https://www.ozon.ru',
    #                 'Referer': f'https://www.ozon.ru/product/{product_id}/'
    #             },
    #             json={
    #                 "url": f"/product/{product_id}/",
    #                 "params": {
    #                     "productId": product_id
    #                 }
    #             },
    #             timeout=10
    #         )
            
    #         if response.status_code == 200:
    #             data = response.json()
    #             result = []
                
    #             widget_states = data.get('widgetStates', {})
                
    #             for key, value in widget_states.items():
    #                 if isinstance(value, dict) and 'images' in value:
    #                     images = value.get('images', [])
    #                     for img in images:
    #                         if isinstance(img, dict) and 'url' in img:
    #                             img_url = img['url']
    #                             if img_url.startswith('//'):
    #                                 img_url = f"https:{img_url}"
    #                             elif not img_url.startswith('http'):
    #                                 img_url = f"https://{img_url}"
    #                             result.append(img_url)
                    
    #                 if 'gallery' in value:
    #                     gallery = value['gallery']
    #                     if isinstance(gallery, list):
    #                         for img in gallery:
    #                             if isinstance(img, dict) and 'url' in img:
    #                                 img_url = img['url']
    #                                 if img_url.startswith('//'):
    #                                     img_url = f"https:{img_url}"
    #                                 elif not img_url.startswith('http'):
    #                                     img_url = f"https://{img_url}"
    #                                 result.append(img_url)
                
    #             result.extend([
    #                 f"https://ozon-st.cdn.ngenix.net/multimedia/{product_id}/image.jpg",
    #                 f"https://cdn1.ozone.ru/multimedia/{product_id}/image.jpg",
    #                 f"https://ozon-st.cdn.ngenix.net/multimedia/c500/{product_id}.jpg",
    #             ])
                
    #             unique_urls = list(dict.fromkeys(result))
                
    #             cache.set(cache_key, unique_urls, timeout=3600)
    #             return unique_urls
                    
    #     except Exception as e:
    #         logger.error(f"Ошибка API запроса Ozon для {product_id}: {str(e)}")
        
    #     return []

    # def _get_product_url(self, product_id: Union[int, str]) -> str:
    #     """Получение URL товара Ozon"""
    #     return f"{self.product_url}{product_id}/"

    # @BaseParser.sync_timing_decorator
    # def _generate_direct_image_url(self, product_id: int) -> Optional[str]:
    #     """Генерация прямого URL Ozon в обход проверок"""
    #     try:
    #         return f"https://ozon-st.cdn.ngenix.net/multimedia/{product_id}/image.jpg"
    #     except:
    #         return None

    # def get_product_data(self, product_id: int) -> Optional[Dict]:
    #     """Получение данных товара через Selenium"""
    #     if not self.driver:
    #         return None
            
    #     try:
    #         product_url = f"{self.product_url}{product_id}/"
    #         self.driver.get(product_url)
            
    #         # Ожидаем загрузки
    #         WebDriverWait(self.driver, 15).until(
    #             EC.presence_of_element_located((By.CSS_SELECTOR, "[data-widget='webProductHeading']"))
    #         )
            
    #         time.sleep(2)
            
    #         # Извлекаем данные со страницы товара
    #         return self._parse_product_page()
            
    #     except Exception as e:
    #         logger.error(f"Ошибка получения данных товара {product_id}: {e}")
    #         return None
    
    # def _parse_product_page(self) -> Optional[Dict]:
    #     """Парсинг страницы товара"""
    #     try:
    #         # Здесь реализуйте парсинг конкретной страницы товара
    #         # Это будет зависеть от структуры страницы Ozon
    #         return None
            
    #     except Exception as e:
    #         logger.error(f"Ошибка парсинга страницы товара: {e}")
    #         return None
        
    # @BaseParser.sync_timing_decorator
    # def search_products_with_strategy(self, query: str, limit: int = 10, strategy: str = "default") -> List[Dict]:
    #     """Поиск с разными стратегиями для Ozon"""
    #     return super().search_products_with_strategy(query, limit, strategy)