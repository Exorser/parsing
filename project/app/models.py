from django.db import models
from django.utils import timezone
from django.utils.safestring import mark_safe
from django.contrib.auth.models import User

class Platform(models.TextChoices):
    WILDBERRIES = 'WB', 'Wildberries'
    OZON = 'OZ', 'Ozon'

class Product(models.Model):
    """Модель товара для обеих платформ"""
    platform = models.CharField(
        max_length=2,
        choices=Platform.choices,
        default=Platform.WILDBERRIES,
        verbose_name="Платформа"
    )
    name = models.CharField(max_length=500, verbose_name="Название")
    price = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Цена")
    discount_price = models.DecimalField(
        max_digits=10, 
        decimal_places=2, 
        null=True, 
        blank=True, 
        verbose_name="Цена со скидкой"
    )
    
    # Поля для Wildberries
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
    
    # Поля для Ozon
    ozon_card_price = models.DecimalField(
        max_digits=10, 
        decimal_places=2, 
        null=True, 
        blank=True, 
        verbose_name="Цена по карте Ozon"
    )
    has_ozon_card_discount = models.BooleanField(
        default=False,
        verbose_name="Есть скидка по карте Ozon"
    )
    has_ozon_card_payment = models.BooleanField(
        default=False,
        verbose_name="Доступна оплата картой Ozon"
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
    search_query = models.CharField(
        max_length=200, 
        verbose_name="Поисковый запрос",
        default=""
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
            models.Index(fields=['platform', 'product_id']),
            models.Index(fields=['platform', 'search_query']),
            models.Index(fields=['platform']),
            models.Index(fields=['has_wb_card_discount']),
            models.Index(fields=['has_ozon_card_discount']),
            models.Index(fields=['is_available']),
        ]
        unique_together = ['platform', 'product_id']

    def __str__(self):
        platform_name = dict(Platform.choices)[self.platform]
        return f"{platform_name}: {self.name} ({self.product_id})"
    
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
    def card_discount_percentage(self):
        """Вычисляет процент скидки по карте платформы"""
        if self.platform == Platform.WILDBERRIES:
            if self.has_wb_card_discount and self.wildberries_card_price is not None:
                return round(((self.price - self.wildberries_card_price) / self.price) * 100, 1)
        elif self.platform == Platform.OZON:
            if self.has_ozon_card_discount and self.ozon_card_price is not None:
                return round(((self.price - self.ozon_card_price) / self.price) * 100, 1)
        return 0
    
    @property
    def card_price(self):
        """Возвращает цену по карте в зависимости от платформы"""
        if self.platform == Platform.WILDBERRIES:
            return self.wildberries_card_price
        elif self.platform == Platform.OZON:
            return self.ozon_card_price
        return None
    
    @property
    def has_card_discount(self):
        """Проверяет, есть ли скидка по карте"""
        if self.platform == Platform.WILDBERRIES:
            return self.has_wb_card_discount
        elif self.platform == Platform.OZON:
            return self.has_ozon_card_discount
        return False
    
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
    def should_show_card_price(self):
        """Показывать ли цену по карте"""
        if self.platform == Platform.WILDBERRIES:
            return self.has_wb_card_payment and self.wildberries_card_price is not None
        elif self.platform == Platform.OZON:
            return self.has_ozon_card_payment and self.ozon_card_price is not None
        return False

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
    
