# management/commands/update_products.py
from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from app.models import Product
from app.product_service import ProductService
import logging

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Обновляет устаревшие товары в базе данных'
    
    def add_arguments(self, parser):
        parser.add_argument(
            '--limit',
            type=int,
            default=100,
            help='Количество товаров для обновления (по умолчанию 100)'
        )
        parser.add_argument(
            '--days',
            type=int,
            default=1,
            help='Обновлять товары старше N дней (по умолчанию 1)'
        )
    
    def handle(self, *args, **options):
        update_limit = options['limit']
        days_threshold = options['days']
        
        product_service = ProductService()
        update_threshold = timezone.now() - timedelta(days=days_threshold)
        
        # Находим товары, которые нужно обновить
        products_to_update = Product.objects.filter(
            updated_at__lt=update_threshold,
            is_available=True
        )[:update_limit]
        
        self.stdout.write(f"Найдено {products_to_update.count()} товаров для обновления")
        
        updated_count = 0
        for product in products_to_update:
            try:
                # Получаем свежие данные через API
                product_data = product_service.parser.get_product_data(product.product_id)
                
                if product_data:
                    # Обновляем товар
                    product.name = product_data.get('name', product.name)
                    product.price = product_data.get('price', product.price)
                    product.discount_price = product_data.get('discount_price', product.discount_price)
                    product.rating = product_data.get('rating', product.rating)
                    product.reviews_count = product_data.get('reviews_count', product.reviews_count)
                    product.quantity = product_data.get('quantity', product.quantity)
                    product.is_available = product_data.get('is_available', product.is_available)
                    product.updated_at = timezone.now()
                    product.save()
                    
                    updated_count += 1
                    
            except Exception as e:
                logger.error(f"Ошибка обновления товара {product.product_id}: {str(e)}")
                self.stderr.write(f"Ошибка обновления товара {product.product_id}: {str(e)}")
        
        self.stdout.write(f"Успешно обновлено {updated_count} товаров")
        
        # Очищаем старые сессии поиска
        cleaned_sessions = product_service.cleanup_old_sessions(days=7)
        self.stdout.write(f"Очищено {cleaned_sessions} старых сессий поиска")