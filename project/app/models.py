from django.db import models
from django.utils import timezone
from django.utils.safestring import mark_safe  # Добавьте этот импорт
from django.contrib.auth.models import User

class Product(models.Model):
    """Модель товара"""
    name = models.CharField(max_length=500, verbose_name="Название")
    price = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Цена")
    discount_price = models.DecimalField(
        max_digits=10, 
        decimal_places=2, 
        null=True, 
        blank=True, 
        verbose_name="Цена со скидкой"
    )
    wildberries_card_price = models.DecimalField(
        max_digits=10, 
        decimal_places=2, 
        null=True, 
        blank=True, 
        verbose_name="Цена по карте Wildberries"
    )
    has_wb_card_discount = models.BooleanField(
        default=False,
        verbose_name="Есть скидка по карте Wildberries"
    )
    has_wb_card_payment = models.BooleanField(
        default=False,
        verbose_name="Доступна оплата картой Wildberries"
    )
    rating = models.DecimalField(
        max_digits=3, 
        decimal_places=2, 
        null=True, 
        blank=True, 
        verbose_name="Рейтинг"
    )
    reviews_count = models.IntegerField(
        default=0, 
        verbose_name="Количество отзывов"
    )
    product_url = models.URLField(
        max_length=1000, 
        verbose_name="Ссылка на товар"
    )
    product_id = models.CharField(
        max_length=100, 
        unique=True, 
        verbose_name="ID товара"
    )
    image_url = models.URLField(
        max_length=1000, 
        blank=True, 
        null=True, 
        verbose_name="Основное изображение"
    )
    has_image = models.BooleanField(
        default=False,
        verbose_name="Есть изображения"
    )
    category = models.CharField(
        max_length=200, 
        verbose_name="Категория"
    )
    search_query = models.CharField(
        max_length=200, 
        verbose_name="Поисковый запрос"
    )
    created_at = models.DateTimeField(
        default=timezone.now, 
        verbose_name="Дата создания"
    )
    updated_at = models.DateTimeField(
        auto_now=True, 
        verbose_name="Дата обновления"
    )
    quantity = models.IntegerField(
        default=0,
        verbose_name="Количество в наличии"
    )
    is_available = models.BooleanField(
        default=True,
        verbose_name="Товар в наличии"
    )

    class Meta:
        verbose_name = "Товар"
        verbose_name_plural = "Товары"
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['product_id']),
            models.Index(fields=['category']),
            models.Index(fields=['search_query']),
            models.Index(fields=['has_wb_card_discount']),
            models.Index(fields=['has_wb_card_payment']),
            models.Index(fields=['is_available']),
        ]

    def __str__(self):
        return f"{self.name[:50]} - {self.price}₽"
    
    def image_tag(self):
        if self.images.exists():
            return mark_safe(f'<img src="{self.images.first().image.url}" width="150" />')
        return "Нет изображения"
    
    @property
    def has_discount(self):
        """Проверяет, есть ли скидка на товар"""
        return self.discount_price is not None and self.discount_price < self.price
    
    @property
    def discount_percentage(self):
        """Вычисляет процент скидки"""
        if self.has_discount and self.discount_price is not None:
            return round(((self.price - self.discount_price) / self.price) * 100, 1)
        return 0
   
    @property
    def wb_card_discount_percentage(self):
        """Вычисляет процент скидки по карте Wildberries"""
        if self.has_wb_card_discount and self.wildberries_card_price is not None:
            return round(((self.price - self.wildberries_card_price) / self.price) * 100, 1)
        return 0
    
    @property
    def main_image(self):
        if self.images.exists():
            return self.images.first().image.url
        return self.image_url if self.image_url else None
    
    @property
    def availability_status(self):
        """Возвращает статус наличия товара"""
        if self.quantity > 0:
            return "В наличии"
        elif self.quantity == 0:
            return "Нет в наличии"
        else:
            return "Неизвестно"
    
    @property
    def availability_class(self):
        """Возвращает CSS класс для отображения статуса"""
        if self.quantity > 0:
            return "available"
        elif self.quantity == 0:
            return "out-of-stock"
        else:
            return "unknown"
        
    @property
    def should_show_card_price(self):
        """Показывать ли цену по карте Wildberries"""
        return self.has_wb_card_payment and self.wildberries_card_price is not None

class ProductImage(models.Model):
    """Модель для хранения изображений товаров"""
    product = models.ForeignKey(Product, related_name='images', on_delete=models.CASCADE)
    image = models.ImageField(upload_to='products/', verbose_name="Изображение")
    image_url = models.URLField(max_length=1000, verbose_name="URL изображения")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")
    image_size = models.CharField(max_length=20, blank=True, null=True)  # Новое поле
    image_type = models.CharField(max_length=10, blank=True, null=True)   # Новое поле
    is_main = models.BooleanField(default=False)  # Опционально

    class Meta:
        verbose_name = "Изображение товара"
        verbose_name_plural = "Изображения товаров"
        ordering = ['-is_main', 'id']

    def __str__(self):
        return f"Изображение для {self.product.name[:30]}"
    
class SearchSession(models.Model):
        """Сессия поиска пользователя"""
        user = models.ForeignKey(User, on_delete=models.CASCADE)
        query = models.CharField(max_length=255)
        category = models.CharField(max_length=100, blank=True)
        created_at = models.DateTimeField(auto_now_add=True)
        last_active = models.DateTimeField()
        last_parsed = models.DateTimeField(null=True, blank=True)
        total_products = models.IntegerField(default=0)
        pages_parsed = models.IntegerField(default=0)
        
        class Meta:
            indexes = [
                models.Index(fields=['user', 'query']),
                models.Index(fields=['last_active']),
            ]
            unique_together = ['user', 'query', 'category']

class ProductSearchResult(models.Model):
        """Связь между товаром и поисковой сессией"""
        search_session = models.ForeignKey(SearchSession, on_delete=models.CASCADE)
        product = models.ForeignKey(Product, on_delete=models.CASCADE)
        parsed_at = models.DateTimeField(auto_now_add=True)
        shown_to_user = models.BooleanField(default=False)
        
        class Meta:
            indexes = [
                models.Index(fields=['search_session', 'parsed_at']),
            ]
            unique_together = ['search_session', 'product']