from django.db import models
from django.utils import timezone

class Product(models.Model):
    """Модель товара"""
    name = models.CharField(max_length=500, verbose_name="Название")
    price = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Цена")
    discount_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="Цена со скидкой")
    rating = models.DecimalField(max_digits=3, decimal_places=2, null=True, blank=True, verbose_name="Рейтинг")
    reviews_count = models.IntegerField(default=0, verbose_name="Количество отзывов")
    product_url = models.URLField(max_length=1000, verbose_name="Ссылка на товар")
    product_id = models.CharField(max_length=100, unique=True, verbose_name="ID товара")
    image_url = models.URLField(max_length=1000, blank=True, null=True, verbose_name="Основное изображение")
    category = models.CharField(max_length=200, verbose_name="Категория")
    search_query = models.CharField(max_length=200, verbose_name="Поисковый запрос")
    created_at = models.DateTimeField(default=timezone.now, verbose_name="Дата создания")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Дата обновления")

    class Meta:
        verbose_name = "Товар"
        verbose_name_plural = "Товары"
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.name[:50]} - {self.price}₽"

class ProductImage(models.Model):
    """Модель для хранения изображений товаров"""
    product = models.ForeignKey(Product, related_name='images', on_delete=models.CASCADE)
    image = models.ImageField(upload_to='products/', verbose_name="Изображение")
    image_url = models.URLField(max_length=1000, verbose_name="URL изображения")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")

    class Meta:
        verbose_name = "Изображение товара"
        verbose_name_plural = "Изображения товаров"
        ordering = ['created_at']

    def __str__(self):
        return f"Изображение для {self.product.name[:30]}"
    
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
