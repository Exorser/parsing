from django.contrib import admin
from .models import Product, Platform
from django.utils.safestring import mark_safe

@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = (
        'platform', 'name', 'price', 'discount_price', 'card_price_display', 
        'rating', 'reviews_count', 'image_preview', 'quantity', 
        'is_available', 'availability_status', 'search_query', 'created_at'
    )
    list_filter = (
        'platform', 'search_query', 'created_at', 'updated_at', 
        'is_available', 'has_wb_card_discount', 'has_ozon_card_discount'
    )
    search_fields = ('name', 'product_id', 'search_query')
    ordering = ('-created_at',)
    readonly_fields = ('created_at', 'updated_at', 'image_preview', 'card_price_display')
    list_select_related = True
    
    fieldsets = (
        ('Основная информация', {
            'fields': ('platform', 'name', 'product_id', 'product_url', 'search_query')
        }),
        ('Цены и скидки', {
            'fields': ('price', 'discount_price', 'has_discount', 'discount_percentage')
        }),
        ('Wildberries', {
            'fields': ('wildberries_card_price', 'has_wb_card_discount', 'has_wb_card_payment'),
            'classes': ('collapse',)
        }),
        ('Ozon', {
            'fields': ('ozon_card_price', 'has_ozon_card_discount', 'has_ozon_card_payment'),
            'classes': ('collapse',)
        }),
        ('Рейтинг и отзывы', {
            'fields': ('rating', 'reviews_count')
        }),
        ('Наличие', {
            'fields': ('quantity', 'is_available', 'availability_status')
        }),
        ('Изображения', {
            'fields': ('image_url', 'image_preview', 'has_image')
        }),
        ('Даты', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        })
    )

    def image_preview(self, obj):
        if obj.main_image:
            return mark_safe(f'<img src="{obj.main_image}" width="50" height="50" style="object-fit: cover;" />')
        return "-"
    image_preview.short_description = 'Изображение'
    
    def availability_status(self, obj):
        return obj.availability_status
    availability_status.short_description = 'Статус наличия'
    
    def card_price_display(self, obj):
        """Отображение цены по карте в зависимости от платформы"""
        if obj.platform == Platform.WILDBERRIES and obj.wildberries_card_price:
            return f"WB Card: {obj.wildberries_card_price}₽"
        elif obj.platform == Platform.OZON and obj.ozon_card_price:
            return f"Ozon Card: {obj.ozon_card_price}₽"
        return "-"
    card_price_display.short_description = 'Цена по карте'
    
    def has_discount(self, obj):
        return obj.has_discount
    has_discount.boolean = True
    has_discount.short_description = 'Есть скидка'
    
    def get_queryset(self, request):
        return super().get_queryset(request).select_related()
    
    def get_fieldsets(self, request, obj=None):
        """Динамическое отображение полей в зависимости от платформы"""
        fieldsets = super().get_fieldsets(request, obj)
        
        if obj:
            # Скрываем нерелевантные поля для платформы
            if obj.platform == Platform.WILDBERRIES:
                fieldsets = [fs for fs in fieldsets if fs[0] != 'Ozon']
            elif obj.platform == Platform.OZON:
                fieldsets = [fs for fs in fieldsets if fs[0] != 'Wildberries']
        
        return fieldsets