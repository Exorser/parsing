import requests
import json
import time
import logging
from typing import List, Dict, Optional
from fake_useragent import UserAgent
from .models import Product
from bs4 import BeautifulSoup
import re

# Настройка логирования
logger = logging.getLogger(__name__)

class WildberriesParser:
    def __init__(self):
        self.session = requests.Session()
        self.ua = UserAgent()
        self.base_url = "https://www.wildberries.ru"
        self.search_url = "https://search.wb.ru/exactmatch/ru/common/v4/search"
        
        # Настройка сессии
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

    def search_products(self, query: str, limit: int = 10) -> List[Dict]:
        """
        Поиск товаров через XHR запросы
        """
        try:
            # Параметры запроса
            params = {
                'TestGroup': 'no_test',
                'TestID': 'no_test',
                'appType': '1',
                'curr': 'rub',
                'dest': '-1257786',
                'resultset': 'catalog',
                'sort': 'popular',
                'spp': '0',
                'suppressSpellcheck': 'false',
                'query': query,
                'page': 1,
                'limit': limit
            }
            
            logger.info(f"Поиск товаров: {query}")
            
            # Выполняем запрос
            response = self.session.get(
                self.search_url,
                params=params,
                timeout=30
            )
            
            data = response.json()
            products = data['data']['products']
            
            logger.info(f"API вернул {len(products)} товаров, запрошено: {limit}")
            
            # Ограничиваем количество товаров до запрошенного лимита
            if len(products) > limit:
                products = products[:limit]
                logger.info(f"Ограничили до {limit} товаров")
            
            return self._parse_products(products)
            
        except requests.exceptions.Timeout:
            logger.error("Таймаут запроса")
            return []
        except requests.exceptions.RequestException as e:
            logger.error(f"Ошибка запроса: {e}")
            return []
        except json.JSONDecodeError:
            logger.error("Ошибка парсинга JSON")
            return []
        except Exception as e:
            logger.error(f"Неожиданная ошибка: {e}")
            return []

    def _parse_products(self, products_data: List[Dict]) -> List[Dict]:
        """
        Парсинг данных товаров
        """
        parsed_products = []
        
        for product in products_data:
            try:
                # Извлекаем данные
                product_id = product.get('id', '')
                name = product.get('name', '')
                
                # Цены
                original_price = product.get('priceU', 0)  # Обычная цена
                sale_price = product.get('salePriceU', 0)  # Цена со скидкой
                
                # Конвертируем копейки в рубли
                original_price_rub = original_price / 100 if original_price else 0
                sale_price_rub = sale_price / 100 if sale_price else 0
                
                # Определяем основную цену и цену со скидкой
                if sale_price_rub > 0 and sale_price_rub < original_price_rub:
                    # Есть скидка
                    price = original_price_rub
                    discount_price = sale_price_rub
                else:
                    # Нет скидки или скидка больше обычной цены
                    price = sale_price_rub if sale_price_rub > 0 else original_price_rub
                    discount_price = None
                
                # Рейтинг
                rating = product.get('rating', 0)
                
                # Количество отзывов (feedbacks)
                review_count = product.get('feedbacks', 0)
                
                # URL товара
                product_url = f"https://www.wildberries.ru/catalog/{product_id}/detail.aspx"
                
                parsed_product = {
                    'product_id': str(product_id),
                    'name': name,
                    'price': price,
                    'discount_price': discount_price,
                    'rating': rating,
                    'reviews_count': review_count,
                    'product_url': product_url,
                    'category': '',
                    'search_query': ''
                }
                
                parsed_products.append(parsed_product)
                
            except Exception as e:
                logger.error(f"Ошибка парсинга товара: {e}")
                continue
        
        return parsed_products

    def save_products(self, products: List[Dict]) -> int:
        """
        Сохранение товаров в базу данных
        """
        logger.info(f"Начинаем сохранение {len(products)} товаров")
        saved_count = 0
        
        for product_data in products:
            try:
                # Проверяем, существует ли товар
                product, created = Product.objects.get_or_create(
                    product_id=product_data['product_id'],
                    defaults={
                        'name': product_data['name'],
                        'price': product_data['price'],
                        'discount_price': product_data['discount_price'],
                        'rating': product_data['rating'],
                        'reviews_count': product_data['reviews_count'],
                        'product_url': product_data['product_url'],
                        'category': product_data['category'],
                        'search_query': product_data['search_query']
                    }
                )
                
                # Обновляем товар (новый или существующий)
                product.name = product_data['name']
                product.price = product_data['price']
                product.discount_price = product_data['discount_price']
                product.rating = product_data['rating']
                product.reviews_count = product_data['reviews_count']
                product.product_url = product_data['product_url']
                product.category = product_data['category']
                product.search_query = product_data['search_query']
                product.save()
                
                if created:
                    saved_count += 1
                
            except Exception as e:
                logger.error(f"Ошибка сохранения товара: {e}")
                continue
        
        logger.info(f"Сохранено новых товаров: {saved_count}")
        return saved_count

    def parse_and_save(self, query: str, category: str = "", limit: int = 10) -> int:
        """
        Основной метод для парсинга и сохранения товаров
        """        
        # Парсим товары
        products = self.search_products(query, limit)
        
        if not products:
            return 0
        
        # Добавляем поисковый запрос и категорию к каждому товару
        for product in products:
            product['search_query'] = query
            product['category'] = category
        
        # Сохраняем в базу
        saved_count = self.save_products(products)
        
        logger.info(f"Парсинг завершен! Сохранено товаров: {saved_count}")
        return saved_count 