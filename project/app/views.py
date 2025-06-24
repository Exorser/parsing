from django.shortcuts import render, redirect
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q, Avg, Min, Max, Count, Case, When, F, DecimalField, FloatField
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_GET
import json

from .models import Product
from .parser import WildberriesParser


def index(request):
    """Главная страница с формой поиска"""
    # Получаем статистику
    total_products = Product.objects.count()
    total_categories = Product.objects.values('category').distinct().count()
    total_queries = Product.objects.values('search_query').distinct().count()
    
    # Последние товары
    recent_products = Product.objects.all()[:10]
    
    context = {
        'total_products': total_products,
        'total_categories': total_categories,
        'total_queries': total_queries,
        'recent_products': recent_products,
    }
    
    return render(request, 'app/index.html', context)


def search_form(request):
    """Страница с формой поиска"""
    return render(request, 'app/search_form.html')


def _apply_filters(products, filters):
    """Применяет фильтры к queryset"""
    if filters.get('search_query'):
        products = products.filter(
            Q(name__icontains=filters['search_query']) | 
            Q(search_query__icontains=filters['search_query'])
        )
    
    if filters.get('category'):
        products = products.filter(category__icontains=filters['category'])
    
    # Фильтры по цене
    if filters.get('min_price'):
        try:
            products = products.filter(actual_price__gte=float(filters['min_price']))
        except ValueError:
            pass
    
    if filters.get('max_price'):
        try:
            products = products.filter(actual_price__lte=float(filters['max_price']))
        except ValueError:
            pass
    
    # Фильтры по рейтингу
    if filters.get('min_rating'):
        try:
            rating = float(filters['min_rating'])
            if 0 <= rating <= 5:
                products = products.filter(rating__gte=rating)
        except ValueError:
            pass
    
    if filters.get('max_rating'):
        try:
            rating = float(filters['max_rating'])
            if 0 <= rating <= 5:
                products = products.filter(rating__lte=rating)
        except ValueError:
            pass
    
    # Фильтры по отзывам
    if filters.get('min_reviews'):
        try:
            reviews = int(filters['min_reviews'])
            if reviews >= 0:
                products = products.filter(reviews_count__gte=reviews)
        except ValueError:
            pass
    
    if filters.get('max_reviews'):
        try:
            reviews = int(filters['max_reviews'])
            if reviews >= 0:
                products = products.filter(reviews_count__lte=reviews)
        except ValueError:
            pass
    
    return products


def _apply_sorting(products, sort_by):
    """Применяет сортировку к queryset"""
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
    
    return products.order_by(sort_options.get(sort_by, '-created_at'))


def products_list(request):
    """Список всех товаров с фильтрацией"""
    # Получаем параметры
    filters = {
        'search_query': request.GET.get('search', ''),
        'category': request.GET.get('category', ''),
        'min_price': request.GET.get('min_price', ''),
        'max_price': request.GET.get('max_price', ''),
        'min_rating': request.GET.get('min_rating', ''),
        'max_rating': request.GET.get('max_rating', ''),
        'min_reviews': request.GET.get('min_reviews', ''),
        'max_reviews': request.GET.get('max_reviews', ''),
    }
    sort_by = request.GET.get('sort', '-created_at')
    
    # Базовый queryset с актуальной ценой
    products = Product.objects.annotate(
        actual_price=Case(
            When(discount_price__isnull=False, discount_price__lt=F('price'), 
                 then=F('discount_price')),
            default=F('price'),
            output_field=DecimalField(max_digits=10, decimal_places=2)
        )
    )
    
    # Применяем фильтры и сортировку
    products = _apply_filters(products, filters)
    products = _apply_sorting(products, sort_by)
    
    # Пагинация
    paginator = Paginator(products, 20)
    page_obj = paginator.get_page(request.GET.get('page'))
    
    # Категории для фильтра
    categories = (
        Product.objects.exclude(category="")
        .values_list('category', flat=True)
        .distinct()
    )
    categories = sorted(set([cat.strip() for cat in categories if cat and cat.strip()]))
    
    context = {
        'page_obj': page_obj,
        'categories': categories,
        **filters,
        'sort_by': sort_by,
    }
    
    return render(request, 'app/products_list.html', context)


def product_detail(request, product_id):
    """Детальная информация о товаре"""
    try:
        product = Product.objects.get(id=product_id)
        
        # Похожие товары
        similar_products = Product.objects.filter(
            category=product.category
        ).exclude(id=product.id)[:5]
        
        context = {
            'product': product,
            'similar_products': similar_products,
        }
        
        return render(request, 'app/product_detail.html', context)
        
    except Product.DoesNotExist:
        messages.error(request, 'Товар не найден')
        return redirect('products_list')


def statistics(request):
    """Страница со статистикой"""
    # Общая статистика
    total_products = Product.objects.count()
    total_categories = Product.objects.values('category').distinct().count()
    total_queries = Product.objects.values('search_query').distinct().count()
    
    # Статистика по актуальным ценам (со скидкой, если есть)
    products_with_actual_price = Product.objects.annotate(
        actual_price=Case(
            When(discount_price__isnull=False, discount_price__lt=F('price'), 
                 then=F('discount_price')),
            default=F('price'),
            output_field=DecimalField(max_digits=10, decimal_places=2)
        )
    )
    
    price_stats = products_with_actual_price.aggregate(
        avg_price=Avg('actual_price'),
        min_price=Min('actual_price'),
        max_price=Max('actual_price'),
        avg_rating=Avg('rating'),
        total_reviews=Count('reviews_count')
    )
    
    # Топ категорий
    top_categories = Product.objects.values('category').annotate(
        count=Count('id')
    ).order_by('-count')[:10]
    
    # Топ поисковых запросов
    top_queries = Product.objects.values('search_query').annotate(
        count=Count('id')
    ).order_by('-count')[:10]
    
    # Товары со скидкой
    discounted_products = Product.objects.filter(
        discount_price__isnull=False
    ).count()
    
    context = {
        'total_products': total_products,
        'total_categories': total_categories,
        'total_queries': total_queries,
        'price_stats': price_stats,
        'top_categories': top_categories,
        'top_queries': top_queries,
        'discounted_products': discounted_products,
    }
    
    return render(request, 'app/statistics.html', context)


@csrf_exempt
@require_http_methods(["POST"])
def start_parsing(request):
    """API endpoint для запуска парсинга"""
    try:
        data = json.loads(request.body)
        query = data.get('query', '').strip()
        category = data.get('category', '').strip()
        limit = int(data.get('limit', 10))
        
        if not query:
            return JsonResponse({
                'success': False,
                'error': 'Поисковый запрос обязателен'
            })
        
        # Запускаем парсинг
        parser = WildberriesParser()
        
        try:
            saved_count = parser.parse_and_save(query, category, limit)
            
            if saved_count > 0:
                return JsonResponse({
                    'success': True,
                    'message': f'Успешно сохранено {saved_count} товаров',
                    'saved_count': saved_count
                })
            else:
                return JsonResponse({
                    'success': False,
                    'error': 'Товары не найдены или не сохранены'
                })
                
        except Exception as e:
            return JsonResponse({
                'success': False,
                'error': f'Ошибка при парсинге: {str(e)}'
            })
            
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Неверный формат JSON'
        })
    except ValueError:
        return JsonResponse({
            'success': False,
            'error': 'Неверное значение параметра limit'
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Неожиданная ошибка: {str(e)}'
        })


@require_GET
def price_histogram_data(request):
    """API: Гистограмма цен с учетом фильтров"""
    from django.db.models import Case, When, F, DecimalField
    # Получаем параметры фильтрации (аналогично products_list)
    min_price = request.GET.get('min_price', '')
    max_price = request.GET.get('max_price', '')
    min_rating = request.GET.get('min_rating', '')
    max_rating = request.GET.get('max_rating', '')
    min_reviews = request.GET.get('min_reviews', '')
    max_reviews = request.GET.get('max_reviews', '')
    category = request.GET.get('category', '')
    search_query = request.GET.get('search', '')

    products = Product.objects.annotate(
        actual_price=Case(
            When(discount_price__isnull=False, discount_price__lt=F('price'), then=F('discount_price')),
            default=F('price'),
            output_field=DecimalField(max_digits=10, decimal_places=2)
        )
    )
    if search_query:
        products = products.filter(Q(name__icontains=search_query) | Q(search_query__icontains=search_query))
    if category:
        products = products.filter(category__icontains=category)
    if min_price:
        try: products = products.filter(actual_price__gte=float(min_price))
        except: pass
    if max_price:
        try: products = products.filter(actual_price__lte=float(max_price))
        except: pass
    if min_rating:
        try: products = products.filter(rating__gte=float(min_rating))
        except: pass
    if max_rating:
        try: products = products.filter(rating__lte=float(max_rating))
        except: pass
    if min_reviews:
        try: products = products.filter(reviews_count__gte=int(min_reviews))
        except: pass
    if max_reviews:
        try: products = products.filter(reviews_count__lte=int(max_reviews))
        except: pass

    # Диапазоны цен (можно поменять)
    bins = [0, 5000, 10000, 20000, 50000, 100000]
    labels = [f'{bins[i]}–{bins[i+1]}' for i in range(len(bins)-1)]
    counts = [0]*(len(bins)-1)
    for p in products:
        price = float(p.actual_price)
        for i in range(len(bins)-1):
            if bins[i] <= price < bins[i+1]:
                counts[i] += 1
                break
    return JsonResponse({"labels": labels, "data": counts})


@require_GET
def discount_vs_rating_data(request):
    """API: Средняя скидка vs рейтинг (группировка по округлённому рейтингу)"""
    from django.db.models import Case, When, F, DecimalField, FloatField, Avg
    min_price = request.GET.get('min_price', '')
    max_price = request.GET.get('max_price', '')
    min_rating = request.GET.get('min_rating', '')
    max_rating = request.GET.get('max_rating', '')
    min_reviews = request.GET.get('min_reviews', '')
    max_reviews = request.GET.get('max_reviews', '')
    category = request.GET.get('category', '')
    search_query = request.GET.get('search', '')

    products = Product.objects.annotate(
        actual_price=Case(
            When(discount_price__isnull=False, discount_price__lt=F('price'), then=F('discount_price')),
            default=F('price'),
            output_field=DecimalField(max_digits=10, decimal_places=2)
        ),
        discount_amount=Case(
            When(discount_price__isnull=False, discount_price__lt=F('price'), then=F('price')-F('discount_price')),
            default=0,
            output_field=FloatField()
        )
    ).filter(discount_price__isnull=False, discount_price__lt=F('price'))
    if search_query:
        products = products.filter(Q(name__icontains=search_query) | Q(search_query__icontains=search_query))
    if category:
        products = products.filter(category__icontains=category)
    if min_price:
        try: products = products.filter(actual_price__gte=float(min_price))
        except: pass
    if max_price:
        try: products = products.filter(actual_price__lte=float(max_price))
        except: pass
    if min_rating:
        try: products = products.filter(rating__gte=float(min_rating))
        except: pass
    if max_rating:
        try: products = products.filter(rating__lte=float(max_rating))
        except: pass
    if min_reviews:
        try: products = products.filter(reviews_count__gte=int(min_reviews))
        except: pass
    if max_reviews:
        try: products = products.filter(reviews_count__lte=int(max_reviews))
        except: pass

    # Группируем по округлённому рейтингу (шаг 0.5)
    from collections import defaultdict
    rating_bins = [x/2 for x in range(0, 11)]  # 0.0, 0.5, ..., 5.0
    rating_groups = defaultdict(list)
    for p in products:
        if p.rating is not None:
            r = round(float(p.rating)*2)/2
            rating_groups[r].append(float(p.discount_amount))
    labels = []
    avg_discounts = []
    for r in sorted(rating_groups.keys()):
        discounts = rating_groups[r]
        if discounts:
            labels.append(str(r))
            avg_discounts.append(round(sum(discounts)/len(discounts), 2))
    return JsonResponse({"labels": labels, "data": avg_discounts})
