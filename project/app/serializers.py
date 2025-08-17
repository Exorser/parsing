from rest_framework import serializers
from .models import Product, ProductImage


class ProductSerializer(serializers.ModelSerializer):
    """
    Сериализатор для модели Product
    """
    class Meta:
        model = Product
        fields = '__all__'