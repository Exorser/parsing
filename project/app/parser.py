import requests
import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Tuple, Optional, Any
from fake_useragent import UserAgent
from bs4 import BeautifulSoup
from io import BytesIO
from django.core.files import File
from urllib.parse import urlparse
from .models import Product, ProductImage  # Импорт ваших моделей
from django.core.cache import cache
import asyncio
import aiohttp
from functools import lru_cache
import time
import random
from PIL import Image
import uuid   

logger = logging.getLogger(__name__)

class WildberriesParser:
    def __init__(self):
        self.session = requests.Session()
        self.ua = UserAgent()
        self.base_url = "https://www.wildberries.ru"
        self.search_url = "https://search.wb.ru/exactmatch/ru/common/v4/search"
        self.timeout = 5
        self.max_workers = 10 
        
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
    
    

    def search_products(self, query: str, limit: int = 10) -> List[Dict]:
        """Поиск товаров через API Wildberries"""
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
                "appType": 1
            }
            
            logger.info(f"Поиск товаров: {query}")
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
            
            logger.info(f"API вернул {len(products)} товаров, запрошено: {limit}")
            return self._parse_products(products[:limit])
           
        except Exception as e:
            logger.error(f"Ошибка при поиске товаров: {e}", exc_info=True)
            return []
        
    def _get_image_urls_from_api(self, product_id: int) -> List[str]:
        """Альтернативный метод получения URL через API с обработкой 404"""
        try:
            response = self.session.get(
                f"https://card.wb.ru/cards/detail?nm={product_id}",
                timeout=5
            )
            if response.status_code == 404:
                return []
            data = response.json()
            return [f"https://images.wbstatic.net/big/new/{pic}.jpg" 
                   for pic in data.get('data', {}).get('products', [{}])[0].get('pics', [])]
        except Exception as e:
            logger.warning(f"Ошибка API для товара {product_id}: {str(e)}")
            return []
    
    def _get_image_urls(self, product_id: int) -> List[str]:
        """
        Основной метод получения URL изображений.
        Комбинирует все возможные способы получения URL.
        """
        try:
            # Убедимся, что product_id - целое число
            product_id = int(product_id)
            vol = product_id // 100000
            part = product_id // 1000
            
            # 1. Пробуем стандартные URL Wildberries
            standard_urls = [
                f"https://basket-01.wb.ru/vol{vol}/part{part}/{product_id}/images/big/1.webp",
                f"https://basket-01.wb.ru/vol{vol}/part{part}/{product_id}/images/big/1.jpg",
                f"https://images.wbstatic.net/big/new/{product_id}-1.jpg",
                f"https://images.wbstatic.net/c516x688/new/{product_id}-1.jpg"
            ]
            
            # 2. Пробуем получить через API (если не работает, просто игнорируем)
            api_urls = []
            try:
                response = requests.get(
                    f"https://card.wb.ru/cards/detail?nm={product_id}",
                    headers={'User-Agent': self.ua.random},
                    timeout=3
                )
                if response.status_code == 200:
                    data = response.json()
                    if data.get('data', {}).get('products'):
                        api_urls = [f"https://images.wbstatic.net/big/new/{pic}.jpg" 
                                for pic in data['data']['products'][0].get('pics', [])]
            except Exception as e:
                logger.debug(f"Ошибка при запросе API для товара {product_id}: {str(e)}")
            
            # 3. Комбинируем все URL и убираем дубликаты
            all_urls = list(set(standard_urls + api_urls))
            return all_urls
            
        except Exception as e:
            logger.error(f"Ошибка генерации URL для товара {product_id}: {str(e)}")
            return []
    
    def _get_valid_image_urls(self, product_id: int) -> List[str]:
        """Основной метод получения рабочих URL изображений"""
        # 1. Пробуем получить URL через API
        api_urls = self._get_image_urls_from_api(product_id)
        if api_urls:
            return api_urls[:3]  # Берем первые 3 изображения
        
        # 2. Если API не сработало, генерируем URL по шаблону
        generated_urls = self._generate_image_urls(product_id)
        
        # 3. Проверяем сгенерированные URL (максимум 5 параллельных проверок)
        valid_urls = []
        with ThreadPoolExecutor(max_workers=min(5, self.max_workers)) as executor:
            future_to_url = {
                executor.submit(self._check_image_available, url): url
                for url in generated_urls[:20]  # Проверяем только первые 20 URL
            }
            
            for future in as_completed(future_to_url):
                if future.result():
                    valid_urls.append(future_to_url[future])
                    if len(valid_urls) >= 3:  # Нам достаточно 3 рабочих URL
                        break
        
        return valid_urls
    
    @lru_cache(maxsize=1000)
    def _get_product_image_urls(self, product_id: int) -> List[str]:
        """Генерация URL изображений с кэшированием"""
        urls = []
        product_id = int(product_id)
        vol = product_id // 100000
        part = product_id // 1000
        
        # Основные шаблоны URL
        templates = [
            f"https://basket-{{server:02d}}.wb.ru/vol{vol}/part{part}/{product_id}/images/{{size}}/{{num}}.webp",
            f"https://basket-{{server:02d}}.wb.ru/vol{vol}/part{part}/{product_id}/images/{{size}}/{{num}}.jpg",
            f"https://images.wbstatic.net/{{size}}/new/{product_id}-{{num}}.jpg"
        ]
        
        sizes = ['big', 'c516x688']
        servers = [1]  # Используем только basket-01 (самый надежный)
        
        for template in templates:
            for server in servers:
                for size in sizes:
                    for num in range(1, 6):  # Проверяем только первые 5 изображений
                        url = template.format(server=server, size=size, num=num)
                        urls.append(url)
        
        return list(set(urls))  # Удаляем дубликаты

    
    def _get_product_data_from_api(self, product_id: int) -> Optional[Dict]:
        """Получает данные товара через официальное API Wildberries"""
        try:
            response = requests.get(
                f"https://card.wb.ru/cards/detail?nm={product_id}",
                headers={'User-Agent': self.ua.random},
                timeout=5
            )
            response.raise_for_status()
            data = response.json()
            
            if data.get('data', {}).get('products'):
                return data['data']['products'][0]
        except Exception as e:
            logger.warning(f"Ошибка API для товара {product_id}: {e}")
        return None

    def _download_image_with_retry(self, url: str, retries: int = 2) -> Optional[BytesIO]:
        """Загрузка изображения с повторными попытками"""
        for attempt in range(retries + 1):
            try:
                response = requests.get(
                    url,
                    stream=True,
                    timeout=(3.05, 6),  # Connect timeout 3s, read timeout 6s
                    headers={'User-Agent': self.ua.random}
                )
                response.raise_for_status()
                return BytesIO(response.content)
            except Exception as e:
                if attempt == retries:
                    logger.warning(f"Не удалось загрузить изображение {url} после {retries} попыток: {str(e)}")
                    return None
                logger.debug(f"Повторная попытка ({attempt + 1}/{retries}) для {url}")
                time.sleep(0.5 * (attempt + 1))  # Постепенное увеличение задержки
        
    def _process_product_images(self, product: Product) -> List[ProductImage]:
        """Обработка изображений товара"""
        valid_urls = self._get_valid_image_urls(product.product_id)
        saved_images = []
        
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = [
                executor.submit(self._download_image, product, url)
                for url in valid_urls[:5]  # Ограничиваем количество загрузок
            ]
            
            for future in as_completed(futures):
                try:
                    result = future.result()
                    if result:
                        saved_images.append(result)
                except Exception as e:
                    logger.warning(f"Ошибка при обработке изображения: {e}")
        
        return saved_images

    def _check_image_available(self, url: str) -> bool:
        """Проверка доступности изображения с улучшенной обработкой ошибок"""
        try:
            # Меняем User-Agent для каждого запроса
            headers = {'User-Agent': self.ua.random}
            
            # Делаем HEAD-запрос (быстрее, чем GET)
            response = requests.head(url, headers=headers, timeout=3, allow_redirects=True)
            
            # Проверяем статус код и content-type
            if response.status_code != 200:
                return False
                
            content_type = response.headers.get('Content-Type', '')
            if not content_type.startswith('image/'):
                return False
                
            return True
        except Exception as e:
            logger.debug(f"Изображение недоступно {url}: {str(e)}")
            return False
    
    

    def _generate_image_urls(self, product_id: int) -> List[str]:
        """Генерация возможных URL изображений с учетом разных серверов"""
        product_id = int(product_id)
        urls = []
        
        # Вычисляем vol и part
        vol = product_id // 100000
        part = product_id // 1000
        
        # 1. Основной шаблон Wildberries (пробуем разные серверы basket-01..basket-26)
        servers = range(1, 27)  # Серверы от basket-01 до basket-26
        base_url_template = "https://basket-{server:02d}.wbbasket.ru/vol{vol}/part{part}/{product_id}/images"
        
        for server in servers:
            base_url = base_url_template.format(server=server, vol=vol, part=part, product_id=product_id)
            for size in ['big', 'c516x688']:  # Разные размеры изображений
                for i in range(1, 6):  # Первые 5 изображений каждого типа
                    urls.extend([
                        f"{base_url}/{size}/{i}.webp",
                        f"{base_url}/{size}/{i}.jpg"
                    ])
        
        # 2. Альтернативные URL (wbstatic.net)
        urls.extend([
            f"https://images.wbstatic.net/big/new/{product_id}-1.jpg",
            f"https://images.wbstatic.net/c516x688/new/{product_id}-1.jpg",
            f"https://images.wbstatic.net/big/new/{product_id}-2.jpg",
        ])
        
        return list(set(urls))  # Удаляем дубликаты
    
    

    def _extract_price_info(self, product: Dict) -> Dict[str, Optional[float]]:
        """
        Улучшенное извлечение информации о ценах с учетом всех возможных вариантов от Wildberries
        """
        # Основные варианты получения цен
        price = discount_price = None
        
        # 1. Проверяем новый формат цен (через sizes)
        if 'sizes' in product and product['sizes']:
            for size in product['sizes']:
                if 'price' in size:
                    price_data = size['price']
                    basic = price_data.get('basic', 0) / 100
                    product_price = price_data.get('product', 0) / 100
                    
                    # Если есть скидка
                    if product_price > 0 and product_price < basic:
                        price = basic
                        discount_price = product_price
                        break
                    else:
                        price = basic if basic > 0 else product_price
        
        # 2. Проверяем старый формат (priceU/salePriceU)
        if price is None:
            original = product.get('priceU', 0) / 100
            sale = product.get('salePriceU', 0) / 100
            
            if sale > 0 and sale < original:
                price = original
                discount_price = sale
            else:
                price = original if original > 0 else sale
        
        # 3. Проверяем дополнительные поля (extended)
        if 'extended' in product and 'basicPriceU' in product['extended']:
            basic_ext = product['extended']['basicPriceU'] / 100
            if price is None or (basic_ext > 0 and basic_ext < price):
                price = basic_ext
        
        # 4. Проверяем clientSale (дополнительная скидка)
        if 'clientSale' in product and discount_price:
            client_sale = product['clientSale']
            if client_sale > 0:
                discount_price = round(discount_price * (1 - client_sale / 100), 2)
        
        return {
            'price': price if price else 0.0,
            'discount_price': discount_price if discount_price and discount_price < price else None
        }
    

    def _parse_products(self, products_data: List[Dict]) -> List[Dict]:
        """Парсинг данных товаров с улучшенной обработкой рейтингов"""
        parsed_products = []
        
        for product in products_data:
            try:
                product_id = product.get('id')
                if not product_id:
                    continue
                    
                # Обработка рейтинга
                rating = product.get('rating', 0)
                if isinstance(rating, dict):
                    rating = rating.get('rate', 0)  # Новый формат рейтинга
                
                # Обработка количества отзывов
                reviews = product.get('feedbacks', 0)
                if isinstance(reviews, dict):
                    reviews = reviews.get('count', 0)  # Новый формат отзывов
                
                image_urls = self._get_product_image_urls(int(product_id))
                first_image = image_urls[0] if image_urls else ""
                
                # Формируем данные товара
                parsed_product = {
                    'product_id': str(product_id),
                    'name': product.get('name', ''),
                    **self._extract_price_info(product),
                    'rating': float(rating) if rating else 0.0,
                    'reviews_count': int(reviews) if reviews else 0,
                    'product_url': f"{self.base_url}/catalog/{product_id}/detail.aspx",
                    'image_url': first_image,  # Первое изображение для основного поля
                    'image_urls': image_urls,  # Все URL изображений
                    'category': '',
                    'search_query': ''
                }
                
                parsed_products.append(parsed_product)
                
            except Exception as e:
                logger.error(f"Ошибка парсинга товара {product.get('id', 'unknown')}: {str(e)}")
                continue
        
        return parsed_products

    def save_products(self, products: List[Dict]) -> int:
        """Сохранение товаров с обработкой ошибок"""
        saved_count = 0
        
        for product_data in products:
            try:
                # Создаем/обновляем товар (без изображения сначала)
                product, created = Product.objects.update_or_create(
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
                        'image_url': ''  # Временно пустое значение
                    }
                )
                
                # Пробуем сохранить изображения
                has_images = self._save_product_images(product)
                
                if has_images:
                    saved_count += 1
                    logger.info(f"Успешно сохранен товар {product.product_id} с изображениями")
                else:
                    logger.warning(f"Товар {product.product_id} сохранен без изображений")
                    
            except Exception as e:
                logger.error(f"Критическая ошибка сохранения товара {product_data.get('product_id')}: {str(e)}")
                
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

    def _save_product_images(self, product: Product) -> bool:
        """
        Улучшенное сохранение изображений товара.
        Возвращает True, если сохранено хотя бы одно изображение.
        """
        # Получаем URL изображений (с проверкой доступности)
        image_urls = self._get_valid_image_urls(product.product_id)
        saved_count = 0
        
        for i, url in enumerate(image_urls[:3]):  # Ограничиваем количество попыток
            try:
                # Проверяем, не существует ли уже такое изображение
                if ProductImage.objects.filter(product=product, image_url=url).exists():
                    continue
                    
                # Добавляем задержку между запросами (1-3 секунды)
                if i > 0:
                    time.sleep(random.uniform(1, 3))
                
                # Загружаем изображение
                img_data = self._download_image_with_retry(url)
                if not img_data:
                    continue
                    
                # Генерируем уникальное имя файла
                img_name = f"wb_{product.product_id}_{uuid.uuid4().hex[:8]}.jpg"
                
                # Сохраняем изображение
                product_image = ProductImage(
                    product=product,
                    image_url=url
                )
                product_image.image.save(img_name, File(img_data), save=True)
                saved_count += 1
                
                # Если это первое изображение - обновляем основное изображение товара
                if saved_count == 1:
                    product.image_url = url
                    product.save()
                    
            except Exception as e:
                logger.warning(f"Ошибка сохранения изображения {url}: {str(e)}")
                continue
                
        return saved_count > 0

    def _download_image_with_retry(self, url: str, retries: int = 3) -> Optional[BytesIO]:
        """Загрузка изображения с повторными попытками и обработкой ошибок"""
        for attempt in range(retries):
            try:
                # Используем случайный User-Agent для каждого запроса
                headers = {
                    'User-Agent': self.ua.random,
                    'Accept': 'image/webp,image/apng,image/*,*/*;q=0.8',
                    'Referer': 'https://www.wildberries.ru/'
                }
                
                # Устанавливаем разумные таймауты
                response = requests.get(
                    url,
                    headers=headers,
                    stream=True,
                    timeout=(3.05, 10)  # 3s connect timeout, 10s read timeout
                )
                
                # Проверяем статус код и content-type
                if response.status_code != 200:
                    raise ValueError(f"HTTP {response.status_code}")
                    
                if not response.headers.get('Content-Type', '').startswith('image/'):
                    raise ValueError("Invalid content type")
                    
                # Читаем содержимое в BytesIO
                img_data = BytesIO()
                for chunk in response.iter_content(chunk_size=8192):
                    img_data.write(chunk)
                    
                # Проверяем, что это валидное изображение
                img_data.seek(0)
                Image.open(img_data).verify()  # Проверка целостности изображения
                img_data.seek(0)
                
                return img_data
                
            except Exception as e:
                if attempt == retries - 1:
                    logger.warning(f"Не удалось загрузить изображение {url} после {retries} попыток: {str(e)}")
                    return None
                time.sleep(1 * (attempt + 1))  # Постепенно увеличиваем задержку
                    
            except Exception as e:
                    if attempt == retries - 1:
                        logger.warning(f"Не удалось загрузить изображение {url} после {retries} попыток: {str(e)}")
                        return None
                    time.sleep(1 * (attempt + 1))  # Постепенно увеличиваем задержку
    
    def _save_image(self, product: Product, url: str) -> Optional[ProductImage]:
        """Скачивание и сохранение изображения"""
        try:
            response = self.session.get(url, stream=True, timeout=10)
            response.raise_for_status()
            
            img_name = f"{product.product_id}_{url.split('/')[-1]}"
            img_file = BytesIO(response.content)
            
            product_image = ProductImage(
                product=product,
                image_url=url
            )
            product_image.image.save(img_name, File(img_file), save=True)
            return product_image
        except Exception as e:
            logger.warning(f"Ошибка сохранения изображения {url}: {e}")
            return None
        
    def parse_and_save(self, query: str, category: str = "", limit: int = 10) -> int:
        """Основной метод для парсинга и сохранения"""
        products = self.search_products(query, limit)
        
        if not products:
            return 0

        category = category or "Без категории"
        for product in products:
            product['search_query'] = query
            product['category'] = category

        saved_count = self.save_products(products)
        logger.info(f"Парсинг завершен! Сохранено товаров: {saved_count}")
        return saved_count

    def parse_products(self, query: str, category: str = "", limit: int = 10) -> int:
        """Алиас для parse_and_save"""
        return self.parse_and_save(query, category, limit)
