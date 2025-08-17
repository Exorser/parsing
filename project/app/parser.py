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
import random

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
                
            # Получаем все доступные изображения
            pics = products[0].get('pics', [])
            return [f"https://images.wbstatic.net/big/new/{pic}.jpg" for pic in pics]
            
        except Exception as e:
            logger.warning(f"Ошибка API для товара {product_id}: {str(e)}")
            return []

    @lru_cache(maxsize=1000)
    def _generate_image_urls(self, product_id: int) -> List[str]:
        """Генерация URL с приоритетом разных серверов и форматов"""
        product_id = int(product_id)
        urls = set()
        vol = product_id // 100000
        part = product_id // 1000

        # Основные серверы (1-40) + приоритетные
        servers = list(range(1, 41)) + [3, 5, 7, 10, 15, 20, 25, 30]
        
        # Альтернативные домены
        domains = ['wbbasket.ru', 'wb.ru']
        
        for domain in domains:
            for server in servers:
                base_url = f"https://basket-{server:02d}.{domain}/vol{vol}/part{part}/{product_id}/images"
                urls.update({
                    f"{base_url}/big/1.webp",
                    f"{base_url}/big/1.jpg",
                    f"{base_url}/big/2.webp",
                    f"{base_url}/big/2.jpg",
                    f"{base_url}/c516x688/1.webp",
                    f"{base_url}/c516x688/1.jpg"
                })

        # Добавляем URL из API Wildberries
        api_urls = self._get_image_urls_from_api(product_id)
        urls.update(api_urls)

        # Специальные форматы WB
        urls.update({
            f"https://images.wbstatic.net/big/new/{product_id}-1.jpg",
            f"https://images.wbstatic.net/big/new/{product_id}-2.jpg",
            f"https://images.wbstatic.net/c516x688/new/{product_id}-1.jpg",
            f"https://images.wbstatic.net/c516x688/new/{product_id}-2.jpg",
            f"https://images.wbstatic.net/big/new/{product_id}-1.webp"
        })

        return list(urls)

    async def _check_url_available(self, session: aiohttp.ClientSession, url: str) -> Optional[str]:
        """Проверка доступности URL с обработкой DNS ошибок"""
        try:
            async with session.head(url, allow_redirects=True, 
                                timeout=aiohttp.ClientTimeout(total=3)) as response:
                if response.status == 200:
                    content_type = response.headers.get('Content-Type', '')
                    if content_type.startswith('image/'):
                        return str(response.url)
        except aiohttp.ClientConnectorError as e:
            logger.debug(f"Ошибка подключения к {url}: {str(e)}")
        except asyncio.TimeoutError:
            logger.debug(f"Таймаут при проверке {url}")
        except Exception as e:
            logger.debug(f"Ошибка проверки URL {url}: {str(e)}")
        return None

    async def _get_valid_image_urls_async(self, product_id: int) -> List[str]:
        """Улучшенный поиск рабочих URL"""
        urls = self._generate_image_urls(product_id)
        valid_urls = []
        
        # Разделяем URL по приоритетам
        high_priority = [url for url in urls if any(p in url for p in [
            'basket-01', 'basket-02', 'basket-03', 'wbstatic.net/big'
        ])]
        
        mid_priority = [url for url in urls if any(p in url for p in [
            'basket-04', 'basket-05', 'c516x688'
        ]) and url not in high_priority]
        
        low_priority = [url for url in urls if url not in high_priority + mid_priority]
        
        async with aiohttp.ClientSession(headers={
            'User-Agent': self.ua.random,
            'Referer': 'https://www.wildberries.ru/'
        }) as session:
            # Проверяем по приоритетам
            for url_group in [high_priority, mid_priority, low_priority]:
                tasks = [self._check_url_available(session, url) for url in url_group[:30]]  # Проверяем до 30 URL из каждой группы
                results = await asyncio.gather(*tasks)
                
                for result in results:
                    if result and result not in valid_urls:
                        valid_urls.append(result)
                        if len(valid_urls) >= 5:  # Находим до 5 рабочих URL
                            return valid_urls
        
        return valid_urls
    
    def _get_valid_image_urls(self, product_id: int) -> List[str]:
        """Синхронная обертка для асинхронного метода"""
        return asyncio.run(self._get_valid_image_urls_async(product_id))

    def _download_image_fast(self, url: str) -> Optional[BytesIO]:
        """Улучшенная загрузка изображения с обработкой ошибок DNS"""
        try:
            headers = {
                'User-Agent': self.ua.random,
                'Accept': 'image/webp,image/apng,image/*,*/*;q=0.8',
                'Referer': 'https://www.wildberries.ru/',
            }
            
            # Уменьшаем таймаут для DNS
            response = requests.get(url, headers=headers, 
                                stream=True, timeout=(3.05, 10))
            response.raise_for_status()
            
            if not response.headers.get('Content-Type', '').startswith('image/'):
                return None
                
            img_data = BytesIO()
            for chunk in response.iter_content(chunk_size=8192):
                img_data.write(chunk)
                
            img_data.seek(0)
            
            try:
                img = Image.open(img_data)
                img.verify()
                img_data.seek(0)
                return img_data
            except:
                return None
                
        except requests.exceptions.ConnectionError as e:
            logger.debug(f"Ошибка подключения к {url}: {str(e)}")
            return None
        except requests.exceptions.Timeout:
            logger.debug(f"Таймаут при загрузке {url}")
            return None
        except Exception as e:
            logger.debug(f"Ошибка загрузки изображения {url}: {str(e)}")
            return None

    def _process_product_images_fast(self, product: Product) -> bool:
        """Быстрая обработка изображений с резервными вариантами"""
        valid_urls = self._get_valid_image_urls(product.product_id)
        logger.info(f"Найдено {len(valid_urls)} валидных URL для товара {product.product_id}")
        
        if not valid_urls:
            logger.warning(f"Не найдено рабочих URL изображений для товара {product.product_id}")
            return False
            
        for i, url in enumerate(valid_urls[:5]):  # Пробуем первые 5 URL
            try:
                # Добавляем небольшую задержку между запросами
                if i > 0:
                    time.sleep(0.5)
                    
                img_data = self._download_image_fast(url)
                if not img_data:
                    continue
                    
                file_ext = 'jpg' if '.jpg' in url.lower() else 'webp'
                img_name = f"wb_{product.product_id}_{uuid.uuid4().hex[:6]}.{file_ext}"
                
                product_image = ProductImage(product=product, image_url=url)
                product_image.image.save(img_name, File(img_data), save=True)
                
                # Обновляем основное изображение товара, если это первое изображение
                if i == 0:
                    product.image_url = url
                    product.save()
                    
                return True
                
            except Exception as e:
                logger.error(f"Ошибка сохранения изображения {url}: {str(e)}", exc_info=True)
        
        return False

    def _extract_price_info(self, product: Dict) -> Dict[str, Optional[float]]:
        """Извлекает информацию о ценах товара"""
        price = discount_price = wildberries_card_price = None
        
        if 'sizes' in product and product['sizes']:
            for size in product['sizes']:
                if 'price' in size:
                    price_data = size['price']
                    basic = price_data.get('basic', 0) / 100
                    product_price = price_data.get('product', 0) / 100
                    
                    if product_price > 0 and product_price < basic:
                        price = basic
                        discount_price = product_price
                        wildberries_card_price = round(product_price * 0.9, 2)
                        break
                    else:
                        price = basic if basic > 0 else product_price
                        wildberries_card_price = round(price * 0.9, 2)
        
        if price is None:
            original = product.get('priceU', 0) / 100
            sale = product.get('salePriceU', 0) / 100
            
            if sale > 0 and sale < original:
                price = original
                discount_price = sale
                wildberries_card_price = round(sale * 0.9, 2)
            else:
                price = original if original > 0 else sale
                wildberries_card_price = round(price * 0.9, 2)
        
        if 'extended' in product and 'basicPriceU' in product['extended']:
            basic_ext = product['extended']['basicPriceU'] / 100
            if price is None or (basic_ext > 0 and basic_ext < price):
                price = basic_ext
                wildberries_card_price = round(price * 0.9, 2)
        
        if 'clientSale' in product and discount_price:
            client_sale = product['clientSale']
            if client_sale > 0:
                discount_price = round(discount_price * (1 - client_sale / 100), 2)
                wildberries_card_price = round(discount_price * 0.9, 2)
        
        return {
            'price': price if price else 0.0,
            'discount_price': discount_price if discount_price and discount_price < price else None,
            'wildberries_card_price': wildberries_card_price if wildberries_card_price else None
        }

    def _parse_products(self, products_data: List[Dict]) -> List[Dict]:
        """Парсинг данных товаров"""
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
                
                image_urls = self._generate_image_urls(int(product_id))
                first_image = image_urls[0] if image_urls else ""
                
                parsed_product = {
                    'product_id': str(product_id),
                    'name': product.get('name', ''),
                    **self._extract_price_info(product),
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
                        'wildberries_card_price': product_data['wildberries_card_price'],
                        'rating': product_data['rating'],
                        'reviews_count': product_data['reviews_count'],
                        'product_url': product_data['product_url'],
                        'category': product_data['category'],
                        'search_query': product_data['search_query'],
                        'image_url': product_data['image_url']
                    }
                )
                
                # Пробуем сохранить изображения
                if self._process_product_images_fast(product):
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