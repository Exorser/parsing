from django.urls import path
from . import views

app_name = 'app'

urlpatterns = [
    path('', views.index, name='index'),
    path('search/', views.search_form, name='search_form'),
    path('products/', views.products_list, name='products_list'),
    path('product/<int:product_id>/', views.product_detail, name='product_detail'),
    path('statistics/', views.statistics, name='statistics'),
    path('api/price_histogram/', views.price_histogram_data, name='price_histogram_data'),
    path('api/discount_vs_rating/', views.discount_vs_rating_data, name='discount_vs_rating_data'),
    path('api/start-parsing/', views.start_parsing, name='start_parsing'),
] 