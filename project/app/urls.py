from django.urls import path, re_path, include
from rest_framework.routers import DefaultRouter
from .views import (
    FrontendAppView, 
    ProductViewSet,
    start_parsing
)

app_name = 'app'

# Создаем router для API
router = DefaultRouter()
router.register(r'products', ProductViewSet, basename='product')

urlpatterns = [
    # API endpoints через router
    path('api/', include(router.urls)),
    path('api/start-parsing/', start_parsing, name='api_start_parsing'),
]

# Маршрут для SPA (React) - должен быть последним
urlpatterns += [
    re_path(r'^(?!api/).*$', FrontendAppView.as_view(), name='frontend'),
] 