# services/product_service.py
from django.utils import timezone
from datetime import timedelta
from django.core.cache import cache
from django.db.models import Q, Max, Count
from .models import Product, SearchSession, ProductSearchResult
from app.parser import WildberriesParser
import logging

logger = logging.getLogger(__name__)

class ProductService:
    def __init__(self):
        self.parser = WildberriesParser()
    
    def search_products(self, query: str, user_id: int, category: str = "", limit: int = 10, offset: int = 0):
        """
        Умный поиск товаров с пагинацией и кешированием
        """
        # Создаем или получаем сессию поиска
        search_session = self._get_or_create_search_session(user_id, query, category)
        
        # Проверяем, есть ли сохраненные результаты для этой сессии
        cached_results = self._get_cached_results(search_session, limit, offset)
        
        if cached_results['has_more'] or cached_results['products']:
            return cached_results
        
        # Если результатов нет или нужно больше - парсим
        return self._parse_and_save_products(search_session, limit, offset)
    
    def _get_or_create_search_session(self, user_id, query, category):
        """Создает или получает сессию поиска"""
        session, created = SearchSession.objects.get_or_create(
            user_id=user_id,
            query=query,
            category=category,
            defaults={
                'last_active': timezone.now(),
                'total_products': 0,
                'pages_parsed': 0
            }
        )
        
        if not created:
            session.last_active = timezone.now()
            session.save()
            
        return session
    
    def _get_cached_results(self, search_session, limit, offset):
        """Получает кешированные результаты поиска"""
        # Получаем сохраненные товары для этой сессии
        saved_results = ProductSearchResult.objects.filter(
            search_session=search_session
        ).select_related('product').order_by('parsed_at')[offset:offset + limit]
        
        products = [result.product for result in saved_results]
        
        # Проверяем, есть ли еще товары в этой сессии
        total_in_session = ProductSearchResult.objects.filter(
            search_session=search_session
        ).count()
        
        has_more = (offset + limit) < total_in_session
        
        return {
            'products': products,
            'has_more': has_more,
            'total_count': total_in_session,
            'source': 'cache'
        }
    
    def _parse_and_save_products(self, search_session, limit, offset):
        """Парсит и сохраняет новые товары"""
        # Определяем, сколько товаров нужно спарсить
        needed_count = limit + offset - search_session.total_products
        if needed_count <= 0:
            needed_count = limit
        
        # Парсим новые товары
        new_products_data = self.parser.search_products(
            search_session.query, 
            needed_count * 2  # Парсим больше, чтобы учесть возможные дубликаты
        )
        
        if not new_products_data:
            return {
                'products': [],
                'has_more': False,
                'total_count': search_session.total_products,
                'source': 'no_results'
            }
        
        # Сохраняем товары и связываем с сессией
        saved_count = 0
        new_products = []
        
        for product_data in new_products_data:
            # Проверяем, нет ли уже такого товара в базе
            existing_product = Product.objects.filter(
                product_id=product_data['product_id']
            ).first()
            
            if existing_product:
                # Обновляем существующий товар
                product = self._update_existing_product(existing_product, product_data)
            else:
                # Создаем новый товар
                product = self._create_new_product(product_data, search_session.category)
            
            # Связываем товар с сессией поиска
            if product:
                ProductSearchResult.objects.get_or_create(
                    search_session=search_session,
                    product=product,
                    defaults={'parsed_at': timezone.now()}
                )
                
                new_products.append(product)
                saved_count += 1
                
                if saved_count >= needed_count:
                    break
        
        # Обновляем статистику сессии
        search_session.total_products += saved_count
        search_session.pages_parsed += 1
        search_session.last_parsed = timezone.now()
        search_session.save()
        
        # Получаем полный список товаров для сессии (с учетом пагинации)
        all_results = ProductSearchResult.objects.filter(
            search_session=search_session
        ).select_related('product').order_by('parsed_at')
        
        total_count = all_results.count()
        paginated_results = all_results[offset:offset + limit]
        
        return {
            'products': [result.product for result in paginated_results],
            'has_more': (offset + limit) < total_count,
            'total_count': total_count,
            'source': 'fresh_parsed'
        }
    
    def _update_existing_product(self, product, product_data):
        """Обновляет существующий товар"""
        # Проверяем, когда товар обновлялся в последний раз
        update_threshold = timezone.now() - timedelta(hours=24)
        
        if product.updated_at < update_threshold:
            # Обновляем данные товара
            product.name = product_data['name']
            product.price = product_data['price']
            product.discount_price = product_data['discount_price']
            product.rating = product_data['rating']
            product.reviews_count = product_data['reviews_count']
            product.image_url = product_data['image_url']
            product.updated_at = timezone.now()
            product.save()
            
            # Можно также обновить изображения, если нужно
            # self.parser.update_product_images(product)
        
        return product
    
    def _create_new_product(self, product_data, category):
        """Создает новый товар"""
        try:
            product = Product.objects.create(
                product_id=product_data['product_id'],
                name=product_data['name'],
                price=product_data['price'],
                discount_price=product_data['discount_price'],
                wildberries_card_price=product_data.get('wildberries_card_price'),
                rating=product_data['rating'],
                reviews_count=product_data['reviews_count'],
                product_url=product_data['product_url'],
                image_url=product_data['image_url'],
                category=category,
                search_query=product_data['search_query'],
                has_wb_card_discount=product_data.get('has_wb_card_discount', False),
                quantity=product_data.get('quantity', 0),
                is_available=product_data.get('is_available', False)
            )
            
            # Загружаем изображения для нового товара
            self.parser.save_all_product_images(product)
            
            return product
            
        except Exception as e:
            logger.error(f"Ошибка создания товара {product_data['product_id']}: {str(e)}")
            return None
    
    def get_user_search_history(self, user_id, limit=10):
        """История поиска пользователя"""
        return SearchSession.objects.filter(
            user_id=user_id
        ).order_by('-last_active')[:limit]
    
    def cleanup_old_sessions(self, days=30):
        """Очистка старых сессий поиска"""
        cutoff_date = timezone.now() - timedelta(days=days)
        
        # Удаляем сессии старше указанного количества дней
        old_sessions = SearchSession.objects.filter(
            last_active__lt=cutoff_date
        )
        
        count = old_sessions.count()
        old_sessions.delete()
        
        return count
    
    def get_popular_searches(self, limit=10):
        """Самые популярные поисковые запросы"""
        return SearchSession.objects.values('query').annotate(
            total_searches=Count('id')
        ).order_by('-total_searches')[:limit]