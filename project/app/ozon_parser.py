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
from django.db.models import Q
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

class OzonParser:
    def __init__(self):
        self.session = requests.Session()
        self.ua = UserAgent()
        self.base_url = "https://www.ozon.ru"
        self.search_url = "https://www.ozon.ru/api/entrypoint-api.bx/page/json/v2"
        self.product_url = "https://www.ozon.ru/product/"
        self.timeout = 5
        self.max_workers = 10
        self.image_limits = {
            'check_urls': 15,  # Максимум URL для проверки
            'download': 1    # Максимум изображений для загрузки
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
            'Referer': 'https://www.wildberries.ru/'
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
        # Поиск товаров

    @sync_timing_decorator
    def search_products(self, query: str, limit: int = 10) -> List[Dict]:
        """Поиск разнообразных товаров (разные цены, рейтинги) для Ozon"""
        try:
            # Параметры запроса для Ozon API
            payload = {
                "url": f"/search/?text={query}",
                "params": {
                    "text": query,
                    "from_global": True
                }
            }
            
            logger.info(f"Поиск разнообразных товаров Ozon: {query}")
            response = self.session.post(
                self.search_url,
                json=payload,
                headers=self.session.headers,
                timeout=30
            )
            data = response.json()
            
            # Извлекаем товары из структуры Ozon
            products = []
            widget_states = data.get('widgetStates', {})
            
            # Ozon хранит товары в разных местах, ищем в возможных локациях
            search_results = widget_states.get('searchResultsV2', {})
            if search_results:
                products = search_results.get('items', [])
            
            # Альтернативный путь для товаров
            if not products:
                for key, value in widget_states.items():
                    if 'items' in value and isinstance(value['items'], list):
                        products = value['items']
                        break
            
            logger.info(f"Получено {len(products)} товаров из Ozon API")
        
            if not products:
                logger.warning("Ozon API вернуло пустой список товаров")
                return []
            
            # Убедимся, что у нас достаточно товаров для фильтрации
            if len(products) < limit:
                logger.warning(f"Получено только {len(products)} товаров, запрошено {limit}")
                # Возвращаем то, что есть, но не больше лимита
                parsed = self._parse_products(products[:limit])
                logger.info(f"После парсинга осталось {len(parsed)} товаров")
                return parsed
            
            # Разделяем товары на группы по рейтингу
            high_rated = [p for p in products if p.get('rating', 0) >= 4.5]
            medium_rated = [p for p in products if 4.0 <= p.get('rating', 0) < 4.5]
            low_rated = [p for p in products if p.get('rating', 0) < 4.0]
            
            # Разделяем товары на группы по цене
            # Ozon использует другую структуру цен
            prices = []
            for p in products:
                price_data = p.get('price', {})
                price_value = price_data.get('price', '0')
                if price_value:
                    # Преобразуем "1 990 ₽" в число
                    price_num = float(price_value.replace('₽', '').replace(' ', '').strip())
                    prices.append(price_num)
            
            if not prices:
                # Если нет цен, возвращаем обычный список
                parsed = self._parse_products(products[:limit])
                logger.info(f"После парсинга осталось {len(parsed)} товаров")
                return parsed
                
            min_price = min(prices)
            max_price = max(prices)
            price_step = (max_price - min_price) / 3

            cheap = []
            medium = []
            expensive = []
            
            for p in products:
                price_data = p.get('price', {})
                price_value = price_data.get('price', '0')
                if price_value:
                    price_num = float(price_value.replace('₽', '').replace(' ', '').strip())
                    if price_num < min_price + price_step:
                        cheap.append(p)
                    elif min_price + price_step <= price_num < min_price + 2*price_step:
                        medium.append(p)
                    else:
                        expensive.append(p)
            
            logger.info(f"Высокий рейтинг: {len(high_rated)}, Средний: {len(medium_rated)}, Низкий: {len(low_rated)}")
            logger.info(f"Дешевые: {len(cheap)}, Средние: {len(medium)}, Дорогие: {len(expensive)}")
            
            # Если в какой-то группе недостаточно товаров, добавляем из других
            result = []
            groups = [high_rated, medium_rated, low_rated, cheap, medium, expensive]
            
            # Сначала добавляем по одному товару из каждой непустой группы
            for group in groups:
                if group and len(result) < limit:
                    result.append(group.pop(0))
            
            # Затем заполняем оставшиеся места товарами из всех групп по кругу
            while len(result) < limit and any(groups):
                for group in groups:
                    if group and len(result) < limit:
                        result.append(group.pop(0))
            
            # Если все еще не хватает товаров, добавляем из исходного списка
            if len(result) < limit:
                remaining_needed = limit - len(result)
                # Берем товары, которых еще нет в результате
                additional_products = [p for p in products if p not in result][:remaining_needed]
                result.extend(additional_products)
            
            parsed = self._parse_products(result[:limit])
            logger.info(f"После парсинга осталось {len(parsed)} товаров")
            
            return parsed
        
        except Exception as e:
            logger.error(f"Ошибка при поиске разнообразных товаров Ozon: {e}", exc_info=True)
            return []

    # Генерация и проверка URL

    @lru_cache(maxsize=1000)
    @sync_timing_decorator
    def _generate_smart_image_urls(self, product_id: int) -> List[str]:
        """Ультра-надежная генерация URL изображений для Ozon"""
        product_id = int(product_id)
        urls = []
        
        # 1. ОСНОВНОЙ шаблон Ozon (самые надежные)
        # Ozon использует хэшированные пути для изображений
        # Генерируем несколько вариантов
        
        # Основной CDN Ozon
        urls.extend([
            f"https://ozon-st.cdn.ngenix.net/multimedia/{product_id}/image.jpg",
            f"https://ozon-st.cdn.ngenix.net/multimedia/c200/{product_id}.jpg",
            f"https://ozon-st.cdn.ngenix.net/multimedia/c500/{product_id}.jpg",
            f"https://ozon-st.cdn.ngenix.net/multimedia/c1000/{product_id}.jpg",
        ])
        
        # 2. Альтернативные CDN серверы Ozon
        cdn_servers = ['ozon-st.cdn.ngenix.net', 'cdn1.ozone.ru', 'cdn2.ozone.ru', 'cdn3.ozone.ru']
        for server in cdn_servers:
            urls.extend([
                f"https://{server}/multimedia/{product_id}/image.jpg",
                f"https://{server}/multimedia/c200/{product_id}.jpg",
                f"https://{server}/multimedia/c500/{product_id}.jpg",
                f"https://{server}/multimedia/c1000/{product_id}.jpg",
            ])
        
        # 3. URL с разными размерами и форматами
        sizes = ['w200', 'w300', 'w500', 'w800', 'w1000', 'c200', 'c500', 'c1000']
        formats = ['jpg', 'webp', 'png']
        
        for size in sizes:
            for fmt in formats:
                urls.extend([
                    f"https://ozon-st.cdn.ngenix.net/multimedia/{size}/{product_id}.{fmt}",
                    f"https://cdn1.ozone.ru/multimedia/{size}/{product_id}.{fmt}",
                ])
        
        # 4. API URL (добавляем последними, так как могут быть медленными)
        api_urls = self._get_image_urls_from_api(product_id)
        if api_urls:
            urls.extend(api_urls[:3])
        
        # 5. Резервные URL (на случай если основные не работают)
        urls.extend([
            f"https://ozon.ru/multimedia/{product_id}/image.jpg",
            f"https://www.ozon.ru/multimedia/{product_id}/image.jpg",
        ])
        
        # Убираем дубликаты и сохраняем порядок
        seen = set()
        unique_urls = []
        for url in urls:
            if url not in seen:
                seen.add(url)
                unique_urls.append(url)
        
        logger.info(f"Сгенерировано {len(unique_urls)} надежных URL для Ozon товара {product_id}")
        return unique_urls
        
    @sync_timing_decorator
    def _generate_all_image_urls(self, product_id: int) -> List[str]:
        """Умная генерация URL - максимум 20 самых вероятных"""
        return self._generate_smart_image_urls(product_id)[:150] 
    
    @async_timing_decorator
    async def _get_valid_image_urls_async(self, product_id: int) -> List[Dict]:
        """Более агрессивная проверка URL с приоритетом на скорость для Ozon"""
        cache_key = f"ozon_images_{product_id}"  # Меняем префикс кеша
        if cached := cache.get(cache_key):
            return cached

        urls = self._generate_smart_image_urls(product_id)
        valid_urls = []
        
        async with aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(limit=30),  # Увеличиваем лимит
            timeout=aiohttp.ClientTimeout(total=3),    # Немного увеличиваем таймаут
            headers={
                'User-Agent': self.ua.random,
                'Referer': 'https://www.ozon.ru/',  # Добавляем Referer для Ozon
                'Origin': 'https://www.ozon.ru'     # Добавляем Origin для Ozon
            }
        ) as session:
            
            # Проверяем первые 30 URL параллельно
            tasks = [self._check_and_analyze_image(session, url) for url in urls[:30]]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            valid_urls = [r for r in results if r and not isinstance(r, Exception)]
            
            # Если не нашли, проверяем следующие 30
            if not valid_urls and len(urls) > 30:
                tasks = [self._check_and_analyze_image(session, url) for url in urls[30:60]]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                valid_urls = [r for r in results if r and not isinstance(r, Exception)]
                
            # Если все еще не нашли, пробуем оставшиеся
            if not valid_urls and len(urls) > 60:
                for url in urls[60:]:
                    result = await self._check_and_analyze_image(session, url)
                    if result:
                        valid_urls.append(result)
                        break  # Достаточно одного валидного URL

        cache.set(cache_key, valid_urls, timeout=7200)  # Кешируем на 2 часа
        logger.info(f"Найдено {len(valid_urls)} валидных URL для Ozon товара {product_id}")
        return valid_urls
    
    def search_products_with_strategy(self, query: str, limit: int = 10, strategy: str = "default") -> List[Dict]:
        """Поиск с разными стратегиями для бесплатного/платного бота Ozon"""
        # Ищем в 3 раза больше товаров для фильтрации
        raw_products = self.search_products(query, limit * 3)
        
        if not raw_products:
            return []
        
        # Добавляем platform к каждому товару
        for product in raw_products:
            product['platform'] = 'OZ'  # Меняем на OZ
        
        if strategy == "popular_midrange":
            # Для бесплатного бота: средние цены, хорошие отзывы
            filtered = [
                p for p in raw_products 
                if 500 <= p.get('price', 0) <= 100000  # Широкий диапазон
                and p.get('rating', 0) >= 3.8         # Хороший рейтинг
                and p.get('reviews_count', 0) >= 5    # Несколько отзывов
            ]
            # Сортируем по популярности (рейтинг + отзывы)
            filtered.sort(key=lambda x: (x.get('rating', 0) * x.get('reviews_count', 1)), reverse=True)
            
        else:  # default strategy
            filtered = raw_products
        
        # Убираем дубликаты по product_id
        unique_products = {}
        for product in filtered:
            pid = product.get('product_id')
            if pid and pid not in unique_products:
                unique_products[pid] = product
        
        return list(unique_products.values())[:limit]
        
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
    
    @async_timing_decorator 
    async def download_main_image_async(self, product_id: int) -> Optional[Dict[str, Any]]:
        """Усиленная загрузка с приоритетом на скорость для Ozon"""
        # Проверяем кеш
        cache_key = f"ozon_image_{product_id}"  # Меняем префикс кеша
        if cached_image := cache.get(cache_key):
            return cached_image
        
        # Получаем все возможные URL
        image_urls = await self._get_valid_image_urls_async(product_id)
        
        if not image_urls:
            return None
        
        # Пробуем загрузить первое изображение
        async with aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(limit=5),
            timeout=aiohttp.ClientTimeout(total=5),
            headers={
                'User-Agent': self.ua.random,
                'Referer': 'https://www.ozon.ru/',  # Добавляем Referer для Ozon
                'Origin': 'https://www.ozon.ru'     # Добавляем Origin для Ozon
            }
        ) as session:
            
            for img_info in image_urls[:3]:  # Пробуем первые 3 URL
                result = await self._download_image_async(session, img_info)
                if result:
                    cache.set(cache_key, result, timeout=7200)  # Кешируем на 2 часа
                    return result
        
        return None
    
    # Сохранение данных

    @async_timing_decorator
    async def parse_and_save_async(self, query: str, limit: int = 10) -> int:
        """Парсинг с мониторингом успешности загрузки изображений для Ozon"""
        products_data = self.search_products(query, limit)

        if len(products_data) < limit:
            logger.warning(f"Получено только {len(products_data)} товаров Ozon из запрошенных {limit}")

        if not products_data:
            return 0

        # Сохраняем товары
        saved_count = await self._save_products_async(products_data)
        
        # Проверяем изображения
        product_ids = [p['product_id'] for p in products_data]
        
        # Детальная отладка ВСЕХ товаров
        logger.info("=== ЗАПУСК ДЕТАЛЬНОЙ ОТЛАДКИ OZON ===")
        await self.detailed_debug_products(product_ids)
        
        # Принудительная проверка всех изображений
        logger.info("=== ПРИНУДИТЕЛЬНАЯ ПРОВЕРКА ВСЕХ ИЗОБРАЖЕНИЙ OZON ===")
        await self.validate_all_images(product_ids)
        
        # Финальная проверка
        products_with_good_images = await sync_to_async(Product.objects.filter(
            product_id__in=product_ids,
            platform='OZ'  # Добавляем фильтр по платформе
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
        
        logger.info(f"ФИНАЛЬНЫЙ РЕЗУЛЬТАТ OZON: {products_with_good_images}/{saved_count} товаров с качественными изображениями")
        
        return saved_count
    
    async def force_reload_images(self, product_ids: List[str]):
        """Принудительная перезагрузка изображений для указанных товаров Ozon"""
        try:
            products = await sync_to_async(list)(
                Product.objects.filter(product_id__in=product_ids, platform='OZ')  # Добавляем фильтр по платформе
            )
            
            logger.info(f"Принудительная перезагрузка изображений Ozon для {len(products)} товаров")
            
            for i, product in enumerate(products):
                logger.info(f"Перезагрузка Ozon {i+1}/{len(products)}: {product.product_id}")
                
                # Сбрасываем URL перед повторной попыткой
                await sync_to_async(setattr)(product, 'image_url', '')
                await sync_to_async(product.save)()
                
                success = await self._process_product_images_async(product)
                if success:
                    logger.info(f"Успешно перезагружено изображение Ozon для {product.product_id}")
                else:
                    logger.warning(f"Не удалось перезагрузить изображение Ozon для {product.product_id}")
                    
        except Exception as e:
            logger.error(f"Ошибка в force_reload_images Ozon: {str(e)}", exc_info=True)

    async def _retry_failed_images(self, product_ids: List[str]):
        """Повторная попытка загрузки изображений для товаров Ozon без них"""
        try:
            # Ищем товары с пустыми, null или нерабочими URL
            failed_products = await sync_to_async(list)(
                Product.objects.filter(product_id__in=product_ids, platform='OZ')  # Добавляем фильтр по платформе
                .filter(
                    Q(image_url='') | 
                    Q(image_url__isnull=True) |
                    Q(image_url__startswith='https://via.placeholder.com') |
                    Q(image_url__startswith='placeholder') |
                    Q(image_url__icontains='no+image') |
                    Q(image_url__icontains='no_image')
                )
            )
            
            # Дополнительная проверка: товары с URL, которые могут быть нерабочими
            all_products = await sync_to_async(list)(
                Product.objects.filter(product_id__in=product_ids, platform='OZ')  # Добавляем фильтр по платформе
            )
            
            # Проверяем URL на валидность
            really_failed_products = []
            for product in all_products:
                if not product.image_url or self._is_bad_url(product.image_url):
                    really_failed_products.append(product)
            
            logger.info(f"Найдено {len(failed_products)} товаров Ozon по фильтру и {len(really_failed_products)} по валидации URL")
            
            # Объединяем списки
            all_failed = list({p.product_id: p for p in failed_products + really_failed_products}.values())
            
            logger.info(f"Всего найдено {len(all_failed)} товаров Ozon для повторной обработки")
            
            for i, product in enumerate(all_failed):
                logger.info(f"Повторная обработка Ozon {i+1}/{len(all_failed)}: {product.product_id}")
                success = await self._process_product_images_async(product)
                if success:
                    logger.info(f"Успешно загружено изображение Ozon при повторной попытке для {product.product_id}")
                else:
                    logger.warning(f"Не удалось загрузить изображение Ozon для {product.product_id} даже после повторной попытки")
                    
        except Exception as e:
            logger.error(f"Ошибка в _retry_failed_images Ozon: {str(e)}", exc_info=True)

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
    
    @async_timing_decorator
    async def _save_products_async(self, products_data: List[Dict]) -> int:
        """Последовательное сохранение товаров - гарантирует сохранение всех"""
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
        
        # Сразу после сохранения пытаемся загрузить изображения для всех товаров
        # if saved_products:
        #     logger.info(f"Запускаем принудительную загрузку изображений для {len(saved_products)} товаров")
        #     await self.force_reload_images(saved_products)
        
        return saved_count
    
    @async_timing_decorator
    async def _process_single_product_async(self, product_data: Dict) -> bool:
        """Гарантированное сохранение товара Ozon с улучшенной обработкой ошибок"""
        product_id = product_data.get('product_id', 'unknown')
        
        try:
            # Проверяем обязательные поля
            if not all(key in product_data for key in ['product_id', 'name', 'price']):
                logger.warning(f"Пропускаем товар Ozon {product_id} - отсутствуют обязательные поля")
                return False
            
            # Сохраняем товар
            product, created = await sync_to_async(Product.objects.update_or_create)(
                product_id=product_data['product_id'],
                platform='OZ',  # Меняем платформу на OZ
                defaults={
                    'name': product_data['name'],
                    'price': product_data['price'],
                    'discount_price': product_data.get('discount_price'),
                    'ozon_card_price': product_data.get('ozon_card_price'),  # Ozon-specific поле
                    'rating': product_data.get('rating', 0),
                    'reviews_count': product_data.get('reviews_count', 0),
                    'product_url': product_data.get('product_url', ''),
                    'search_query': product_data.get('search_query', ''),
                    'image_url': product_data.get('image_url', ''),
                    'has_ozon_card_discount': product_data.get('has_ozon_card_discount', False),  # Ozon
                    'has_ozon_card_payment': product_data.get('has_ozon_card_payment', False),   # Ozon
                    'quantity': product_data.get('quantity', 0),
                    'is_available': product_data.get('is_available', False)
                }
            )
            
            logger.debug(f"Товар Ozon {product_id} {'создан' if created else 'обновлен'}")
            
            # СИНХРОННО загружаем изображение после сохранения товара
            try:
                image_loaded = await self._process_product_images_async(product)
                if not image_loaded:
                    logger.warning(f"Не удалось загрузить изображение для товара Ozon {product_id}")
            except Exception as e:
                logger.error(f"Ошибка загрузки изображения для товара Ozon {product_id}: {e}")
            
            return True
                
        except Exception as e:
            logger.error(f"Критическая ошибка сохранения товара Ozon {product_id}: {str(e)}")
            return False
    
    @async_timing_decorator
    async def _process_product_images_async(self, product: Product) -> bool:
        """Гарантированная загрузка изображения с улучшенной стратегией"""
        max_retries = 2
        
        for attempt in range(max_retries):
            try:
                logger.info(f"Попытка {attempt + 1} загрузки изображения для {product.product_id}")
                
                # Пытаемся загрузить изображение
                main_image = await asyncio.wait_for(
                    self.download_main_image_async(int(product.product_id)),
                    timeout=15.0  # Увеличиваем таймаут
                )
                
                if main_image:
                    await sync_to_async(setattr)(product, 'image_url', main_image['url'])
                    await sync_to_async(product.save)()
                    logger.info(f"Успешно загружено изображение для товара {product.product_id}")
                    return True
                
                # Fallback 1: Пробуем API URLs
                api_urls = await sync_to_async(self._get_image_urls_from_api)(int(product.product_id))
                if api_urls:
                    # Берем первый рабочий URL из API
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
                await asyncio.sleep(1 * (attempt + 1))  # Экспоненциальная задержка
        
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
    
    # Вспомогательные методы

    @sync_timing_decorator
    def _parse_products(self, products_data: List[Dict]) -> List[Dict]:
        """Парсинг данных товаров Ozon с учетом всех изображений"""
        parsed_products = []
        
        for product in products_data:
            try:
                product_id = product.get('sku')  # Ozon использует 'sku' вместо 'id'
                if not product_id:
                    continue
                    
                rating = product.get('rating', 0)
                # Ozon обычно использует прямой числовой рейтинг
                if isinstance(rating, dict):
                    rating = rating.get('rate', 0)
                
                reviews = product.get('feedbacks', 0)  # Ozon также использует 'feedbacks'
                if isinstance(reviews, dict):
                    reviews = reviews.get('count', 0)
                
                # Используем новый метод для генерации всех URL
                image_urls = self._generate_all_image_urls(int(product_id))
                first_image = image_urls[0] if image_urls else ""

                quantity_info = self._extract_ozon_quantity_info(product)  # Ozon-specific метод
                price_info = self._extract_ozon_price_info(product)  # Ozon-specific метод
                
                parsed_product = {
                    'product_id': str(product_id),
                    'name': product.get('title', product.get('name', '')),  # Ozon использует 'title'
                    **price_info,
                    **quantity_info, 
                    'rating': float(rating) if rating else 0.0,
                    'reviews_count': int(reviews) if reviews else 0,
                    'product_url': f"{self.product_url}{product_id}/",  # Ozon URL структура
                    'image_url': first_image,
                    'image_urls': image_urls,
                    'search_query': '',
                    'platform': 'OZ'  # Меняем на OZ
                }
                
                parsed_products.append(parsed_product)
                
            except Exception as e:
                logger.error(f"Ошибка парсинга товара Ozon {product.get('sku', 'unknown')}: {str(e)}")
        
        return parsed_products
    
    def _extract_ozon_quantity_info(self, product: Dict) -> Dict[str, Any]:
        """Извлекает информацию о наличии товара Ozon"""
        quantity = 0
        is_available = False
        
        # Ozon структура данных о наличии
        stock_info = product.get('stock', {})
        
        # Основной способ получения количества
        quantity = stock_info.get('count', 0)
        is_available = stock_info.get('available', False) or quantity > 0
        
        # Альтернативные пути к данным о количестве Ozon
        if quantity == 0:
            quantity = product.get('quantity', 0)
            if quantity > 0:
                is_available = True
        
        # Проверяем информацию о доставке
        delivery_info = product.get('delivery', {})
        if not is_available and delivery_info.get('isAvailable', False):
            is_available = True
            quantity = 1  # Если доступна доставка, считаем что есть в наличии
        
        return {
            'quantity': quantity,
            'is_available': is_available
        }

    def _extract_ozon_price_info(self, product: Dict) -> Dict[str, Optional[float]]:
        """Извлекает информацию о ценах товара Ozon"""
        price_data = product.get('price', {})
        
        # Основные цены Ozon
        main_price_str = price_data.get('price', '0').replace('₽', '').replace(' ', '').strip()
        original_price_str = price_data.get('originalPrice', main_price_str).replace('₽', '').replace(' ', '').strip()
        
        # Конвертируем в числа
        main_price = float(main_price_str) if main_price_str else 0.0
        original_price = float(original_price_str) if original_price_str else 0.0
        
        # Ozon Card логика
        ozon_card_price = None
        has_ozon_card_discount = False
        has_ozon_card_payment = True  # Ozon Card обычно доступна
        
        if 'ozonCardPrice' in price_data:
            ozon_card_price_str = price_data['ozonCardPrice'].replace('₽', '').replace(' ', '').strip()
            ozon_card_price = float(ozon_card_price_str) if ozon_card_price_str else None
            has_ozon_card_discount = ozon_card_price is not None and ozon_card_price != main_price
        
        # Определяем основную цену и цену со скидкой
        price = original_price
        discount_price = main_price if main_price < original_price else None
        
        # Дополнительные проверки скидок Ozon
        if 'discount' in price_data and discount_price is None:
            discount = price_data['discount']
            if isinstance(discount, dict) and 'percent' in discount:
                discount_percent = discount['percent']
                if discount_percent > 0:
                    discount_price = original_price * (1 - discount_percent / 100)
                    discount_price = math.floor(discount_price * 100) / 100  # Округление вниз
        
        return {
            'price': price if price else 0.0,
            'discount_price': discount_price if discount_price and discount_price < price else None,
            'ozon_card_price': ozon_card_price,
            'has_ozon_card_discount': has_ozon_card_discount,
            'has_ozon_card_payment': has_ozon_card_payment
        }

    @sync_timing_decorator
    def _get_image_urls_from_api(self, product_id: int) -> List[str]:
        """Получение ТОЛЬКО правильных изображений через API Ozon"""
        try:
            cache_key = f"ozon_api_{product_id}"  # Меняем префикс кеша
            cached = cache.get(cache_key)
            if cached:
                return cached
                
            # Ozon API endpoint для получения информации о товаре
            response = requests.get(
                f"https://www.ozon.ru/api/entrypoint-api.bx/page/json/v2",
                headers={
                    'User-Agent': self.ua.random,
                    'Content-Type': 'application/json',
                    'Origin': 'https://www.ozon.ru',
                    'Referer': f'https://www.ozon.ru/product/{product_id}/'
                },
                json={
                    "url": f"/product/{product_id}/",
                    "params": {
                        "productId": product_id
                    }
                },
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                result = []
                
                # Ozon хранит информацию о товаре в widgetStates
                widget_states = data.get('widgetStates', {})
                
                # Ищем информацию о товаре в различных местах
                for key, value in widget_states.items():
                    if isinstance(value, dict) and 'images' in value:
                        # Извлекаем изображения из данных товара
                        images = value.get('images', [])
                        for img in images:
                            if isinstance(img, dict) and 'url' in img:
                                img_url = img['url']
                                if img_url.startswith('//'):
                                    img_url = f"https:{img_url}"
                                elif not img_url.startswith('http'):
                                    img_url = f"https://{img_url}"
                                result.append(img_url)
                    
                    # Альтернативный путь к изображениям
                    if 'gallery' in value:
                        gallery = value['gallery']
                        if isinstance(gallery, list):
                            for img in gallery:
                                if isinstance(img, dict) and 'url' in img:
                                    img_url = img['url']
                                    if img_url.startswith('//'):
                                        img_url = f"https:{img_url}"
                                    elif not img_url.startswith('http'):
                                        img_url = f"https://{img_url}"
                                    result.append(img_url)
                
                # Добавляем стандартные Ozon CDN URL как fallback
                result.extend([
                    f"https://ozon-st.cdn.ngenix.net/multimedia/{product_id}/image.jpg",
                    f"https://cdn1.ozone.ru/multimedia/{product_id}/image.jpg",
                    f"https://ozon-st.cdn.ngenix.net/multimedia/c500/{product_id}.jpg",
                ])
                
                # Убираем дубликаты
                unique_urls = list(dict.fromkeys(result))
                
                cache.set(cache_key, unique_urls, timeout=3600)
                return unique_urls
                    
        except Exception as e:
            logger.error(f"Ошибка API запроса Ozon для {product_id}: {str(e)}")
        
        return []
    
    def _get_size_from_url(self, url: str) -> str:
        """Определение размера изображения из URL"""
        if 'c516x688' in url:
            return '516x688'
        elif 'big' in url or 'original' in url:
            return 'big'
        return 'unknown'
    
    # Синхронные обертки

    @sync_timing_decorator
    def parse_products(self, query: str, limit: int = 10) -> int:
        """Алиас для parse_and_save"""
        return self.parse_and_save(query, limit)
    
    @sync_timing_decorator
    def parse_and_save(self, query: str, limit: int = 10) -> int:
        """Синхронная обертка для обратной совместимости"""
        return asyncio.run(self.parse_and_save_async(query, limit))
    
    # Остальные
    
    @sync_timing_decorator
    def get_product_data(self, product_id: int) -> Optional[Dict]:
        """Получение данных конкретного товара Ozon по ID"""
        try:
            # Ozon API запрос для получения данных товара
            response = requests.post(
                f"https://www.ozon.ru/api/entrypoint-api.bx/page/json/v2",
                headers={
                    'User-Agent': self.ua.random,
                    'Content-Type': 'application/json',
                    'Origin': 'https://www.ozon.ru',
                    'Referer': f'https://www.ozon.ru/product/{product_id}/'
                },
                json={
                    "url": f"/product/{product_id}/",
                    "params": {
                        "productId": product_id
                    }
                },
                timeout=10
            )
            
            if response.status_code != 200:
                return None
                
            data = response.json()
            widget_states = data.get('widgetStates', {})
            
            # Ищем данные товара в структуре Ozon
            product_info = None
            for key, value in widget_states.items():
                if isinstance(value, dict) and 'sku' in value:
                    product_info = value
                    break
            
            if not product_info:
                return None
                
            # Извлекаем информацию о ценах и наличии
            price_info = self._extract_ozon_price_info(product_info)
            quantity_info = self._extract_ozon_quantity_info(product_info)
            
            # Преобразуем в тот же формат, что и search_products
            return {
                'product_id': str(product_info.get('sku')),
                'name': product_info.get('title', ''),
                'price': price_info.get('price', 0),
                'discount_price': price_info.get('discount_price'),
                'ozon_card_price': price_info.get('ozon_card_price'),
                'rating': float(product_info.get('rating', 0)),
                'reviews_count': int(product_info.get('feedbacks', 0)),
                'quantity': quantity_info.get('quantity', 0),
                'is_available': quantity_info.get('is_available', False),
                'has_ozon_card_discount': price_info.get('has_ozon_card_discount', False),
                'has_ozon_card_payment': price_info.get('has_ozon_card_payment', False),
                'product_url': f"{self.product_url}{product_id}/"
            }
            
        except Exception as e:
            logger.error(f"Ошибка получения данных товара Ozon {product_id}: {str(e)}")
            return None
    
    @async_timing_decorator
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

    @async_timing_decorator
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
    
    @sync_timing_decorator
    def _generate_direct_image_url(self, product_id: int) -> Optional[str]:
        """Генерация прямого URL Ozon в обход проверок"""
        try:
            # Самый популярный и надежный шаблон Ozon
            return f"https://ozon-st.cdn.ngenix.net/multimedia/{product_id}/image.jpg"
            
        except:
            return None
     
    @async_timing_decorator
    async def _save_main_image_async(self, product: Product, image: Dict):
        """Сохранение главного изображения Ozon"""
        try:
            img_name = f"ozon_{product.product_id}_main.{image['type']}"  # Меняем префикс
            
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
            logger.error(f"Ошибка сохранения главного изображения Ozon: {str(e)}")

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
    
    @sync_timing_decorator
    def get_performance_stats(self):
        """Возвращает статистику производительности"""
        return {
            'total_parsing_time': self.total_parsing_time,
            'parsing_count': self.parsing_count,
            'average_time': self.total_parsing_time / self.parsing_count if self.parsing_count > 0 else 0
        }

    @async_timing_decorator
    async def _fetch_product_data(self, product_id: int) -> Optional[Dict]:
        """Получение полных данных о товаре Ozon через API"""
        try:
            async with aiohttp.ClientSession() as session:
                payload = {
                    "url": f"/product/{product_id}/",
                    "params": {
                        "productId": product_id
                    }
                }
                
                async with session.post(
                    "https://www.ozon.ru/api/entrypoint-api.bx/page/json/v2",
                    json=payload,
                    headers={
                        'User-Agent': self.ua.random,
                        'Content-Type': 'application/json',
                        'Origin': 'https://www.ozon.ru',
                        'Referer': f'https://www.ozon.ru/product/{product_id}/'
                    }
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        widget_states = data.get('widgetStates', {})
                        
                        # Ищем данные товара в структуре Ozon
                        for key, value in widget_states.items():
                            if isinstance(value, dict) and 'sku' in value:
                                return value
                        
            return None
        except Exception as e:
            logger.error(f"Ошибка получения данных товара Ozon {product_id}: {str(e)}")
        return None
    
    @async_timing_decorator
    async def _fetch_product_availability(self, product_id: int) -> Dict[str, Any]:
        """Получение информации о наличии товара Ozon через API"""
        try:
            async with aiohttp.ClientSession() as session:
                payload = {
                    "url": f"/product/{product_id}/",
                    "params": {
                        "productId": product_id
                    }
                }
                
                async with session.post(
                    "https://www.ozon.ru/api/entrypoint-api.bx/page/json/v2",
                    json=payload,
                    headers={
                        'User-Agent': self.ua.random,
                        'Content-Type': 'application/json',
                        'Origin': 'https://www.ozon.ru',
                        'Referer': f'https://www.ozon.ru/product/{product_id}/'
                    }
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        widget_states = data.get('widgetStates', {})
                        
                        # Ищем данные товара в структуре Ozon
                        for key, value in widget_states.items():
                            if isinstance(value, dict) and 'sku' in value:
                                return self._extract_ozon_quantity_info(value)  # Ozon-specific метод
                        
            return {'quantity': 0, 'is_available': False}
        except Exception as e:
            logger.error(f"Ошибка получения наличия товара Ozon {product_id}: {str(e)}")
        return {'quantity': 0, 'is_available': False}

    @sync_timing_decorator
    def get_product_availability(self, product_id: int) -> Dict[str, Any]:
        """Синхронная обертка для получения информации о наличии"""
        return asyncio.run(self._fetch_product_availability(product_id))

    @sync_timing_decorator
    def update_products_availability(self, products: List[Product]) -> int:
        """Обновление информации о наличии для списка товаров Ozon"""
        updated_count = 0
        
        for product in products:
            try:
                # Обновляем только товары Ozon
                if product.platform != 'OZ':
                    continue
                    
                availability = self.get_product_availability(int(product.product_id))
                product.quantity = availability['quantity']
                product.is_available = availability['is_available']
                product.save()
                updated_count += 1
                logger.info(f"Обновлено наличие для товара Ozon {product.product_id}: {availability}")
            except Exception as e:
                logger.error(f"Ошибка обновления наличия для товара Ozon {product.product_id}: {str(e)}")
        
        return updated_count

    @async_timing_decorator
    async def _process_single_product(self, product_data: Dict) -> Dict:
        """Асинхронная обработка одного товара Ozon"""
        product_id = product_data.get('sku')  # Ozon использует sku вместо id
        if not product_id:
            return {}

        # Получаем дополнительные данные
        full_data = await self._fetch_product_data(product_id)
        
        # Формируем данные товара
        product = {
            'product_id': str(product_id),
            'name': product_data.get('title', product_data.get('name', '')),  # Ozon использует title
            **self._extract_ozon_price_info(product_data),  # Ozon-specific метод
            'rating': self._get_rating(product_data),
            'reviews_count': self._get_reviews_count(product_data),
            'product_url': f"{self.product_url}{product_id}/",  # Ozon URL структура
            'image_url': '',
            'images': [],
            'search_query': '',
            'brand': full_data.get('brand', '') if full_data else '',
            'description': full_data.get('description', '') if full_data else '',
            'platform': 'OZ'  # Добавляем платформу
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

    async def detailed_debug_products(self, product_ids: List[str]):
        """Детальная отладка всех товаров Ozon"""
        try:
            products = await sync_to_async(list)(
                Product.objects.filter(product_id__in=product_ids, platform='OZ')  # Добавляем фильтр по платформе
            )
            
            logger.info("=== ДЕТАЛЬНАЯ ОТЛАДКА ТОВАРОВ OZON ===")
            
            for i, product in enumerate(products):
                logger.info(f"\n--- Товар Ozon {i+1}/{len(products)}: {product.product_id} ---")
                logger.info(f"Название: {product.name}")
                logger.info(f"URL изображения: '{product.image_url}'")
                logger.info(f"Длина URL: {len(product.image_url) if product.image_url else 0}")
                
                # Детальный анализ URL
                is_empty = not product.image_url or product.image_url.strip() == ''
                is_null = product.image_url is None
                is_placeholder = 'placeholder' in product.image_url.lower() if product.image_url else False
                is_no_image = 'no+image' in product.image_url.lower() or 'no_image' in product.image_url.lower() if product.image_url else False
                
                logger.info(f"Пустой: {is_empty}")
                logger.info(f"Null: {is_null}")
                logger.info(f"Placeholder: {is_placeholder}")
                logger.info(f"No-image: {is_no_image}")
                
                # Проверяем, считается ли URL плохим
                is_bad = self._is_bad_url(product.image_url)
                logger.info(f"Считается плохим: {is_bad}")
                
                # Проверяем существование изображения
                if product.image_url and not is_bad:
                    logger.info("Проверяем доступность изображения Ozon...")
                    try:
                        async with aiohttp.ClientSession() as session:
                            async with session.head(product.image_url, timeout=5, 
                                                headers={
                                                    'User-Agent': self.ua.random,
                                                    'Referer': 'https://www.ozon.ru/',
                                                    'Origin': 'https://www.ozon.ru'
                                                }) as response:
                                logger.info(f"HTTP статус: {response.status}")
                                if response.status == 200:
                                    content_type = response.headers.get('Content-Type', '')
                                    logger.info(f"Content-Type: {content_type}")
                                else:
                                    logger.info("Изображение Ozon недоступно!")
                    except Exception as e:
                        logger.info(f"Ошибка проверки URL Ozon: {e}")
                
                logger.info("---")
                    
        except Exception as e:
            logger.error(f"Ошибка в detailed_debug_products Ozon: {e}", exc_info=True)

    def _is_bad_url(self, url: str) -> bool:
        """Проверяет, является ли URL плохим (placeholder или нерабочим)"""
        if not url:
            return True
            
        # Проверяем на пустую строку или только пробелы
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
    
    async def validate_all_images(self, product_ids: List[str]):
        """Принудительная проверка и перезагрузка всех изображений Ozon"""
        try:
            products = await sync_to_async(list)(
                Product.objects.filter(product_id__in=product_ids, platform='OZ')  # Добавляем фильтр по платформе
            )
            
            logger.info(f"Принудительная проверка {len(products)} товаров Ozon")
            
            for i, product in enumerate(products):
                logger.info(f"Проверка Ozon {i+1}/{len(products)}: {product.product_id}")
                
                # Проверяем текущий URL
                current_url = product.image_url
                is_valid = await self._validate_image_url(current_url)
                
                if not is_valid:
                    logger.info(f"URL Ozon невалиден: {current_url}")
                    # Сбрасываем и пытаемся загрузить заново
                    await sync_to_async(setattr)(product, 'image_url', '')
                    await sync_to_async(product.save)()
                    
                    success = await self._process_product_images_async(product)
                    if success:
                        logger.info(f"Успешно перезагружено изображение Ozon")
                    else:
                        logger.warning(f"Не удалось перезагрузить изображение Ozon")
                else:
                    logger.info(f"URL Ozon валиден: {current_url}")
                    
        except Exception as e:
            logger.error(f"Ошибка в validate_all_images Ozon: {e}", exc_info=True)

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

    def _get_reviews_count(self, product_data: Dict) -> int:
        """Извлечение количества отзывов"""
        reviews = product_data.get('feedbacks', 0)
        if isinstance(reviews, dict):
            return reviews.get('count', 0)
        return int(reviews) if reviews else 0
    
    def close_session(self):
        """Закрытие сессии"""
        self.session.close()