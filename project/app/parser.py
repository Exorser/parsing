import requests
import json
import logging
from typing import List, Dict, Tuple, Optional, Any
from fake_useragent import UserAgent
from bs4 import BeautifulSoup
from io import BytesIO
from django.core.files import File
from urllib.parse import urlparse
from .models import Product, ProductImage  # Импорт ваших моделей
from django.core.cache import cache

logger = logging.getLogger(__name__)

class WildberriesParser:
    def __init__(self):
        self.session = requests.Session()
        self.ua = UserAgent()
        self.base_url = "https://www.wildberries.ru"
        self.search_url = "https://search.wb.ru/exactmatch/ru/common/v4/search"
        
        self.session.headers.update({
            'User-Agent': self.ua.random,
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'ru-RU,ru;q=0.9,en;q=0.8',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Sec-Fetch-Dest': 'empty',
            'Sec-Fetch-Mode': 'cors',
            'Sec-Fetch-Site': 'same-site',
            'Cache-Control': 'no-cache',
            'Pragma': 'no-cache',
        })
    
    def _debug_image_urls(self, product_id: int):
        """Подробное логирование проверки изображений"""
        logger.debug(f"Начинаем проверку изображений для товара {product_id}")
        
        urls = self._get_product_image_urls(product_id)
        found = False
        
        for i, url in enumerate(urls, 1):
            try:
                available = self._check_image_available(url)
                status = "✓" if available else "✗"
                logger.debug(f"{i}. {status} {url}")
                
                if available:
                    found = True
                    logger.info(f"Найдено рабочее изображение: {url}")
                    
            except Exception as e:
                logger.warning(f"Ошибка при проверке URL {url}: {e}")
        
        if not found:
            logger.warning(f"Не найдено доступных изображений для товара {product_id}")
            # Пробуем получить через API
            api_url = f"https://card.wb.ru/cards/detail?nm={product_id}"
            logger.info(f"Пробуем получить изображения через API: {api_url}")

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

    def _get_product_image_urls(self, product_id: int) -> List[str]:
        """Генерация URL изображений с учетом всех возможных вариантов Wildberries"""
        urls = []
        
        # Основной шаблон URL Wildberries
        vol = product_id // 100000
        part = product_id // 1000
        
        # Пробуем разные серверы (01-10)
        for server in range(1, 11):
            server_str = f"{server:02d}"
            base_url = f"https://basket-{server_str}.wbbasket.ru/vol{vol}/part{part}/{product_id}/images"
            
            # Разные размеры и форматы
            sizes = ['big', 'c516x688', 'c246x328']
            for size in sizes:
                urls.extend([
                    f"{base_url}/{size}/1.webp",
                    f"{base_url}/{size}/1.jpg",
                    f"{base_url}/{size}/2.webp",
                    f"{base_url}/{size}/2.jpg",
                ])
        
        # Альтернативные источники изображений
        urls.extend([
            f"https://images.wbstatic.net/big/new/{product_id}-1.jpg",
            f"https://images.wbstatic.net/big/new/{product_id}-2.jpg",
            f"https://images.wbstatic.net/c516x688/new/{product_id}-1.jpg",
            f"https://images.wbstatic.net/c246x328/new/{product_id}-1.jpg",
        ])
        
        return list(set(urls))  # Удаляем дубликаты
    
    from django.core.cache import cache

    def _download_and_save_image(self, product: Product, image_url: str) -> Optional[ProductImage]:
        """Скачивает и сохраняет изображение с кэшированием"""
        cache_key = f"product_image_{product.product_id}_{image_url}"
        cached_image = cache.get(cache_key)
        
        if cached_image:
            return cached_image
        
        try:
            # Проверяем, не существует ли уже такое изображение
            existing = ProductImage.objects.filter(
                product=product,
                image_url=image_url
            ).first()
            
            if existing:
                cache.set(cache_key, existing, timeout=60*60*24)  # Кэш на 24 часа
                return existing
                
            response = requests.get(image_url, stream=True, timeout=10)
            response.raise_for_status()
            
            # Остальная логика сохранения...
            product_image = ProductImage(...)
            product_image.save()
            
            cache.set(cache_key, product_image, timeout=60*60*24)
            return product_image
            
        except Exception as e:
            logger.error(f"Ошибка при сохранении изображения {image_url}: {e}")
            return None
    
    def _process_product_images(self, product: Product, image_urls: List[str], max_images: int = 12) -> List[ProductImage]:
        """Обработка изображений с увеличенным лимитом и улучшенной проверкой"""
        saved_images = []
        
        for url in image_urls:
            if len(saved_images) >= max_images:
                break
                
            try:
                # Проверяем доступность изображения
                if not self._check_image_available(url):
                    continue
                    
                # Проверяем, не сохраняли ли уже это изображение
                if any(img.image_url == url for img in saved_images):
                    continue
                    
                # Скачиваем и сохраняем
                product_image = self._download_and_save_image(product, url)
                if product_image:
                    saved_images.append(product_image)
                    
            except Exception as e:
                logger.error(f"Ошибка при обработке изображения {url}: {e}")
                continue
        
        logger.info(f"Сохранено {len(saved_images)} изображений для товара {product.product_id}")
        return saved_images

    def _check_image_available(self, url: str) -> bool:
        """Проверка доступности изображения с правильными заголовками"""
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                'Accept': 'image/webp,image/apng,image/*,*/*;q=0.8',
                'Referer': 'https://www.wildberries.ru/'
            }
            response = requests.head(url, headers=headers, timeout=5)
            return response.status_code == 200
        except:
            return False
    
    def _get_images_via_api(self, product_id: int) -> List[str]:
        """Получает изображения через API Wildberries"""
        try:
            api_url = f"https://card.wb.ru/cards/detail?nm={product_id}"
            response = requests.get(api_url, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            # Проверяем структуру ответа
            if not data or not isinstance(data, dict):
                logger.warning(f"Неверный формат ответа API для товара {product_id}")
                return []
                
            products = data.get('data', {}).get('products', [])
            if not products:
                logger.warning(f"Товар {product_id} не найден через API")
                return []
                
            # Получаем изображения из первого найденного товара
            images = []
            for product in products:
                if 'pics' in product:
                    images.extend([
                        f"https://images.wbstatic.net/big/new/{pic}.jpg" 
                        for pic in product['pics']
                    ])
            
            return images
            
        except Exception as e:
            logger.error(f"Ошибка при получении изображений через API для {product_id}: {e}")
            return []

    def _get_first_available_image(self, product_id: int) -> str:
        """Получает первое доступное изображение товара"""
        # 1. Пробуем стандартные URL
        standard_urls = self._get_product_image_urls(product_id)
        for url in standard_urls:
            if self._check_image_available(url):
                return url
        
        # 2. Если не нашли, пробуем через API
        api_images = self._get_images_via_api(product_id)
        for url in api_images:
            if self._check_image_available(url):
                return url
        
        # 3. Если ничего не найдено
        logger.warning(f"Не удалось найти изображения для товара {product_id}")
        return ""

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
    def parse_prices_from_html(self, product_url: str) -> Tuple[float, Optional[float]]:
        """
        Улучшенный парсинг цен со страницы товара (резервный метод)
        """
        try:
            headers = {
                'User-Agent': self.ua.random,
                'Accept': 'text/html,application/xhtml+xml',
                'Accept-Language': 'ru-RU,ru;q=0.9',
            }
            
            resp = requests.get(product_url, headers=headers, timeout=15)
            resp.raise_for_status()
            
            # Проверям не блокирует ли Wildberries запрос
            if "Доступ ограничен" in resp.text:
                logger.warning("Wildberries заблокировал доступ к странице товара")
                return 0.0, None
                
            soup = BeautifulSoup(resp.text, 'html.parser')
            
            # Ищем основной блок с ценами
            price_block = soup.find('div', class_='price-block')
            if not price_block:
                return 0.0, None
                
            # Цена со скидкой
            discount_price = None
            discount_tag = price_block.find('span', class_='price-block__final-price')
            if discount_tag:
                try:
                    discount_price = float(discount_tag.get_text(strip=True)
                                        .replace('₽', '')
                                        .replace(' ', '')
                                        .replace('\xa0', ''))
                except (ValueError, AttributeError):
                    pass
                    
            # Обычная цена
            old_price_tag = price_block.find('del', class_='price-block__old-price')
            if old_price_tag:
                try:
                    price = float(old_price_tag.get_text(strip=True)
                                    .replace('₽', '')
                                    .replace(' ', '')
                                    .replace('\xa0', ''))
                    return price, discount_price
                except (ValueError, AttributeError):
                    pass
                    
            # Если не нашли старую цену, используем цену со скидкой как основную
            if discount_price is not None:
                return discount_price, None
                
            return 0.0, None
            
        except Exception as e:
            logger.error(f"Ошибка парсинга HTML цен: {e}")
            return 0.0, None
        
    

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
                
                # Формируем данные товара
                parsed_product = {
                    'product_id': str(product_id),
                    'name': product.get('name', ''),
                    **self._extract_price_info(product),
                    'rating': float(rating) if rating else 0.0,
                    'reviews_count': int(reviews) if reviews else 0,
                    'product_url': f"{self.base_url}/catalog/{product_id}/detail.aspx",
                    'image_url': self._get_first_available_image(int(product_id)),
                    'category': '',
                    'search_query': ''
                }
                
                parsed_products.append(parsed_product)
                
            except Exception as e:
                logger.error(f"Ошибка парсинга товара {product.get('id', 'unknown')}: {str(e)}")
                continue
        
        return parsed_products

    def save_products(self, products: List[Dict]) -> int:
        """Сохранение с обработкой случаев отсутствия изображений"""
        saved_count = 0
        
        for product_data in products:
            try:
                # Пропускаем товары без ID
                if not product_data.get('product_id'):
                    continue
                    
                # Логируем информацию о товаре
                logger.info(f"Обрабатываем товар {product_data['product_id']}: {product_data.get('name', '')}")
                
                # Если нет изображений, пробуем найти через API
                if not product_data.get('image_urls'):
                    product_id = int(product_data['product_id'])
                    product_data['image_urls'] = self._get_product_image_urls(product_id)
                    if not product_data['image_urls']:
                        logger.warning(f"Не удалось найти URL изображений для товара {product_id}")
                
                # Создаем/обновляем товар
                product, created = Product.objects.update_or_create(
                    product_id=product_data['product_id'],
                    defaults={
                        'name': product_data['name'],
                        'price': product_data['price'],
                        'discount_price': product_data['discount_price'],
                        'rating': product_data['rating'],
                        'reviews_count': product_data['reviews_count'],
                        'product_url': product_data['product_url'],
                        'image_url': product_data['image_urls'][0] if product_data['image_urls'] else "",
                        'category': product_data['category'],
                        'search_query': product_data['search_query']
                    }
                )
                
                # Сохраняем изображения
                if product_data['image_urls']:
                    self._process_product_images(product, product_data['image_urls'])
                    logger.info(f"Сохранено {len(product_data['image_urls'])} изображений")
                
                saved_count += 1
                
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