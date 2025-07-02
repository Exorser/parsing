from django.http import JsonResponse, FileResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.views.generic import View
from django.db.models import Q, Avg, Min, Max, Case, When, F, DecimalField
from rest_framework.response import Response
from rest_framework import status
from rest_framework.pagination import PageNumberPagination
from rest_framework.viewsets import ViewSet
from rest_framework.decorators import action
import json
import os

from .models import Product
from .parser import WildberriesParser
from .serializers import ProductSerializer


class FrontendAppView(View):
    """
    Отдаёт index.html для фронтовых маршрутов (SPA)
    """
    def get(self, request, *args, **kwargs):
        # Проверяем, не является ли запрос к статическим файлам
        path = request.path_info.lstrip('/')
        if path.startswith('static/') or path.startswith('api/'):
            return self.http_method_not_allowed(request, *args, **kwargs)
        
        index_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            'static', 'app', 'index.html'
        )
        if not os.path.exists(index_path):
            # fallback для dev
            index_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                'static', 'app', 'index.html'
            )
        
        try:
            return FileResponse(open(index_path, 'rb'), content_type='text/html')
        except FileNotFoundError:
            from django.http import HttpResponse
            return HttpResponse("React app not found. Please build the frontend.", status=404)


class ProductPagination(PageNumberPagination):
    """
    Пагинация для списка товаров
    """
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100


class ProductViewSet(ViewSet):
    """
    ViewSet для работы с товарами
    """
    pagination_class = ProductPagination

    def list(self, request):
        """
        Список товаров с фильтрацией, сортировкой и пагинацией
        """
        # Базовый queryset с актуальной ценой
        products = Product.objects.annotate(
            actual_price=Case(
                When(discount_price__isnull=False, discount_price__lt=F('price'), 
                     then=F('discount_price')),
                default=F('price'),
                output_field=DecimalField(max_digits=10, decimal_places=2)
            )
        )

        # Фильтрация
        search_query = request.GET.get('search', '')
        if search_query:
            products = products.filter(
                Q(name__icontains=search_query) | 
                Q(search_query__icontains=search_query)
            )

        category = request.GET.get('category', '')
        if category:
            products = products.filter(category__icontains=category)

        # Фильтры по цене
        min_price = request.GET.get('min_price', '')
        if min_price:
            try:
                products = products.filter(actual_price__gte=float(min_price))
            except ValueError:
                pass

        max_price = request.GET.get('max_price', '')
        if max_price:
            try:
                products = products.filter(actual_price__lte=float(max_price))
            except ValueError:
                pass

        # Фильтры по рейтингу
        min_rating = request.GET.get('min_rating', '')
        if min_rating:
            try:
                rating = float(min_rating)
                if 0 <= rating <= 5:
                    products = products.filter(rating__gte=rating)
            except ValueError:
                pass

        max_rating = request.GET.get('max_rating', '')
        if max_rating:
            try:
                rating = float(max_rating)
                if 0 <= rating <= 5:
                    products = products.filter(rating__lte=rating)
            except ValueError:
                pass

        # Фильтры по отзывам
        min_reviews = request.GET.get('min_reviews', '')
        if min_reviews:
            try:
                reviews = int(min_reviews)
                if reviews >= 0:
                    products = products.filter(reviews_count__gte=reviews)
            except ValueError:
                pass

        max_reviews = request.GET.get('max_reviews', '')
        if max_reviews:
            try:
                reviews = int(max_reviews)
                if reviews >= 0:
                    products = products.filter(reviews_count__lte=reviews)
            except ValueError:
                pass

        # Сортировка
        sort_by = request.GET.get('sort', '-created_at')
        sort_options = {
            'name': 'name',
            '-name': '-name',
            'price': 'actual_price',
            '-price': '-actual_price',
            'rating': 'rating',
            '-rating': '-rating',
            'reviews': 'reviews_count',
            '-reviews': '-reviews_count',
        }
        products = products.order_by(sort_options.get(sort_by, '-created_at'))

        # Пагинация
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(products, request)
        
        serializer = ProductSerializer(page, many=True)
        
        # Категории для фильтра
        categories = (
            Product.objects.exclude(category="")
            .values_list('category', flat=True)
            .distinct()
        )
        categories = sorted(set([cat.strip() for cat in categories if cat and cat.strip()]))

        # Диапазон цен для слайдера
        price_range = products.aggregate(
            min_price=Min('actual_price'),
            max_price=Max('actual_price')
        )

        return Response({
            'results': serializer.data,
            'count': paginator.page.paginator.count,
            'next': paginator.get_next_link(),
            'previous': paginator.get_previous_link(),
            'categories': categories,
            'price_range': {
                'min': int(price_range['min_price'] or 0),
                'max': int(price_range['max_price'] or 100000)
            },
            'filters': {
                'search': search_query,
                'category': category,
                'min_price': min_price,
                'max_price': max_price,
                'min_rating': min_rating,
                'max_rating': max_rating,
                'min_reviews': min_reviews,
                'max_reviews': max_reviews,
                'sort': sort_by,
            }
        })

    def retrieve(self, request, pk=None):
        """
        Детали товара с похожими товарами
        """
        try:
            product = Product.objects.get(pk=pk)
        except Product.DoesNotExist:
            return Response({'detail': 'Товар не найден.'}, status=status.HTTP_404_NOT_FOUND)

        # Похожие товары
        similar_products = Product.objects.filter(
            category=product.category
        ).exclude(id=product.id)[:5]

        product_serializer = ProductSerializer(product)
        similar_serializer = ProductSerializer(similar_products, many=True)

        return Response({
            'product': product_serializer.data,
            'similar_products': similar_serializer.data,
        })

    @action(detail=False, methods=['get'])
    def statistics(self, request):
        """
        Статистика по товарам
        """
        # Общая статистика
        total_products = Product.objects.count()
        total_categories = Product.objects.values('category').distinct().count()
        total_queries = Product.objects.values('search_query').distinct().count()

        # Статистика по актуальным ценам
        products_with_actual_price = Product.objects.annotate(
            actual_price=Case(
                When(discount_price__isnull=False, discount_price__lt=F('price'), 
                     then=F('discount_price')),
                default=F('price'),
                output_field=DecimalField(max_digits=10, decimal_places=2)
            )
        )

        avg_price = products_with_actual_price.aggregate(Avg('actual_price'))['actual_price__avg']
        min_price = products_with_actual_price.aggregate(Min('actual_price'))['actual_price__min']
        max_price = products_with_actual_price.aggregate(Max('actual_price'))['actual_price__max']

        # Статистика по рейтингам
        avg_rating = Product.objects.aggregate(Avg('rating'))['rating__avg']
        products_with_rating = Product.objects.filter(rating__isnull=False).count()

        # Статистика по скидкам
        products_with_discount = Product.objects.filter(
            discount_price__isnull=False,
            discount_price__lt=F('price')
        ).count()

        if products_with_discount > 0:
            avg_discount_percent = products_with_actual_price.filter(
                discount_price__isnull=False,
                discount_price__lt=F('price')
            ).aggregate(
                avg_discount=Avg(
                    (F('price') - F('discount_price')) / F('price') * 100
                )
            )['avg_discount']
        else:
            avg_discount_percent = 0

        return Response({
            'total_products': total_products,
            'total_categories': total_categories,
            'total_queries': total_queries,
            'price_stats': {
                'average': round(avg_price, 2) if avg_price else 0,
                'min': min_price,
                'max': max_price,
            },
            'rating_stats': {
                'average': round(avg_rating, 2) if avg_rating else 0,
                'products_with_rating': products_with_rating,
            },
            'discount_stats': {
                'products_with_discount': products_with_discount,
                'average_discount_percent': round(avg_discount_percent, 2) if avg_discount_percent else 0,
            }
        })

    @action(detail=False, methods=['get'])
    def price_histogram(self, request):
        """
        Данные для гистограммы цен
        """
        products = Product.objects.annotate(
            actual_price=Case(
                When(discount_price__isnull=False, discount_price__lt=F('price'), 
                     then=F('discount_price')),
                default=F('price'),
                output_field=DecimalField(max_digits=10, decimal_places=2)
            )
        ).filter(actual_price__isnull=False)

        # Группировка по диапазонам цен
        price_ranges = [
            (0, 1000, '0-1000₽'),
            (1000, 5000, '1000-5000₽'),
            (5000, 10000, '5000-10000₽'),
            (10000, 20000, '10000-20000₽'),
            (20000, 50000, '20000-50000₽'),
            (50000, float('inf'), '50000₽+'),
        ]

        histogram_data = []
        for min_price, max_price, label in price_ranges:
            if max_price == float('inf'):
                count = products.filter(actual_price__gte=min_price).count()
            else:
                count = products.filter(actual_price__gte=min_price, actual_price__lt=max_price).count()
            
            histogram_data.append({
                'range': label,
                'count': count
            })

        return Response({'data': histogram_data})

    @action(detail=False, methods=['get'])
    def discount_vs_rating(self, request):
        """
        Данные зависимости скидки от рейтинга
        """
        products = Product.objects.filter(
            discount_price__isnull=False,
            discount_price__lt=F('price'),
            rating__isnull=False
        ).annotate(
            discount_amount=((F('price') - F('discount_price')) / F('price') * 100)
        )

        # Группировка по рейтингам
        rating_ranges = [
            (0, 1, '0-1'),
            (1, 2, '1-2'),
            (2, 3, '2-3'),
            (3, 4, '3-4'),
            (4, 5, '4-5'),
        ]

        chart_data = []
        for min_rating, max_rating, label in rating_ranges:
            range_products = products.filter(
                rating__gte=min_rating,
                rating__lt=max_rating
            )
            
            if range_products.exists():
                avg_discount = range_products.aggregate(
                    Avg('discount_amount')
                )['discount_amount__avg']
                chart_data.append({
                    'rating_range': label,
                    'average_discount': round(avg_discount, 2)
                })

        return Response({'data': chart_data})


@csrf_exempt
@require_http_methods(["POST"])
def start_parsing(request):
    """
    API для запуска парсинга
    """
    try:
        data = json.loads(request.body)
        search_query = data.get('search_query', '')
        category = data.get('category', '')
        
        if not search_query:
            return JsonResponse({'error': 'Не указан поисковый запрос'}, status=400)
        
        parser = WildberriesParser()
        parser.parse_products(search_query, category=category)
        
        return JsonResponse({'message': 'Парсинг запущен успешно'})
        
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Неверный JSON'}, status=400)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)
