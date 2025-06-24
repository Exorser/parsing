from django.contrib import admin
from .models import Product

@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = (
        'name', 'price', 'discount_price', 'rating', 'reviews_count',
        'category', 'search_query', 'created_at', 'updated_at'
    )
    list_filter = ('category', 'search_query', 'created_at', 'updated_at')
    search_fields = ('name', 'product_id', 'category', 'search_query')
    ordering = ('-created_at',)
    readonly_fields = ('created_at', 'updated_at')
