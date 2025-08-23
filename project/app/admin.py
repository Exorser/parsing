from django.contrib import admin
from .models import Product
from django.utils.safestring import mark_safe

@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = (
        'name', 'price', 'discount_price', 'rating', 'reviews_count', 'image_url', 'image_preview', 'quantity', 
        'is_available', 'availability_status', 'search_query', 'created_at', 'updated_at'
    )
    list_filter = ('search_query', 'created_at', 'updated_at', 'is_available')
    search_fields = ('name', 'product_id', 'search_query')
    ordering = ('-created_at',)
    readonly_fields = ('created_at', 'updated_at', 'image_preview')

    def image_preview(self, obj):
        if obj.image_url:
            return mark_safe(f'<img src="{obj.images.first().image.url}" width="50" />')
        return "-"
        
    def availability_status(self, obj):
        return obj.availability_status
    availability_status.short_description = 'Статус наличия'