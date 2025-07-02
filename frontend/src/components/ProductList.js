import React, { useState, useEffect, useRef } from 'react';
import { Link, useNavigate, useLocation } from 'react-router-dom';
import axios from 'axios';
import './ProductList.css';
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, LineChart, Line, CartesianGrid, Legend } from 'recharts';

function ProductList() {                     
  const [products, setProducts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [formState, setFormState] = useState({
    search: '',
    category: '',
    min_price: 0,
    max_price: 100000,
    min_rating: 0,
    max_rating: 5,
    min_reviews: '',
    max_reviews: '',
    sort: '-created_at',
    limit: 10
  });
  const [filters, setFilters] = useState({
    search: '',
    category: '',
    min_price: 0,
    max_price: 100000,
    min_rating: 0,
    max_rating: 5,
    min_reviews: '',
    max_reviews: '',
    sort: '-created_at',
    limit: 10
  });
  const [categories, setCategories] = useState([]);
  const [pagination, setPagination] = useState({
    count: 0,
    next: null,
    previous: null
  });
  const [priceRange, setPriceRange] = useState({ min: 0, max: 100000 });
  const [globalPriceRange, setGlobalPriceRange] = useState({ min: 0, max: 100000 });
  const [chartsData, setChartsData] = useState({
    priceHistogram: [],
    discountVsRating: []
  });
  const [searchInput, setSearchInput] = useState('');

  // useRef для хранения предыдущего priceRange
  const prevPriceRange = useRef(globalPriceRange);

  const navigate = useNavigate();
  const location = useLocation();

  // Загружаем данные при изменении фильтров (кроме searchInput)
  useEffect(() => {
    fetchProducts();
    fetchChartsData();
  }, [filters]);

  useEffect(() => {
    // Синхронизируем только если globalPriceRange реально изменился
    if (
      prevPriceRange.current.min !== globalPriceRange.min ||
      prevPriceRange.current.max !== globalPriceRange.max
    ) {
      setFormState(prev => ({
        ...prev,
        min_price: globalPriceRange.min,
        max_price: globalPriceRange.max
      }));
      setFilters(prev => ({
        ...prev,
        min_price: globalPriceRange.min,
        max_price: globalPriceRange.max
      }));
      prevPriceRange.current = globalPriceRange;
    }
  }, [globalPriceRange.min, globalPriceRange.max]);

  // Для поддержки query-параметра page
  useEffect(() => {
    const params = new URLSearchParams(location.search);
    const page = params.get('page');
    if (page) {
      setFilters(prev => ({ ...prev, page: Number(page) }));
    }
  }, [location.search]);

  // Динамическое применение фильтров
  useEffect(() => {
    setFilters(prev => ({
      ...prev,
      // search не обновляется динамически
      category: formState.category,
      min_price: formState.min_price,
      max_price: formState.max_price,
      min_rating: formState.min_rating,
      max_rating: formState.max_rating,
      min_reviews: formState.min_reviews,
      max_reviews: formState.max_reviews,
      sort: formState.sort,
      // limit, page если нужно
    }));
  }, [
    formState.category,
    formState.min_price,
    formState.max_price,
    formState.min_rating,
    formState.max_rating,
    formState.min_reviews,
    formState.max_reviews,
    formState.sort
  ]);

  const fetchProducts = async () => {
    try {
      setLoading(true);
      const response = await axios.get('/api/products/', { params: filters });
      setProducts(response.data.results);
      setPagination({
        count: response.data.count,
        next: response.data.next,
        previous: response.data.previous
      });
      setCategories(response.data.categories || []);
      
      // Обновляем диапазон цен только если фильтры сброшены (глобальный диапазон)
      if (response.data.price_range) {
        setPriceRange(response.data.price_range);
        // Если фильтры сброшены (или это первый запрос), обновляем globalPriceRange
        if (
          !filters.min_price &&
          !filters.max_price &&
          !filters.category &&
          !filters.search &&
          !filters.min_rating &&
          !filters.max_rating &&
          !filters.min_reviews &&
          !filters.max_reviews
        ) {
          setGlobalPriceRange(response.data.price_range);
        }
      }
    } catch (err) {
      setError('Ошибка загрузки товаров');
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  const fetchChartsData = async () => {
    try {
      const params = new URLSearchParams();
      Object.keys(filters).forEach(key => {
        if (
          filters[key] !== '' &&
          key !== 'sort' &&
          key !== 'page' &&
          key !== 'limit'
        ) {
          params.append(key, filters[key]);
        }
      });

      const [histogramResponse, discountResponse] = await Promise.all([
        axios.get(`/api/products/price_histogram/?${params}`),
        axios.get(`/api/products/discount_vs_rating/?${params}`)
      ]);

      setChartsData({
        priceHistogram: histogramResponse.data.data || [],
        discountVsRating: discountResponse.data.data || []
      });
    } catch (err) {
      console.error('Ошибка загрузки данных для диаграмм:', err);
    }
  };

  const handleFormChange = (key, value) => {
    setFormState(prev => ({ ...prev, [key]: value }));
  };

  const handlePriceRangeChange = ([min, max]) => {
    setFormState(prev => ({ ...prev, min_price: min, max_price: max }));
  };

  const handleRatingRangeChange = ([min, max]) => {
    setFormState(prev => ({ ...prev, min_rating: min, max_rating: max }));
  };

  // Поиск по названию только по кнопке или Enter
  const applySearch = () => {
    setFilters(prev => ({ ...prev, search: searchInput }));
  };

  const handleSearchKeyDown = (e) => {
    if (e.key === 'Enter') {
      applySearch();
    }
  };

  const handleSortChange = (sort) => {
    setFilters(prev => ({
      ...prev,
      sort
    }));
  };

  // При сбросе фильтров сбрасываем и поиск
  const clearFilters = () => {
    setFormState(prev => ({
      ...prev,
      search: '',
      category: '',
      min_price: globalPriceRange.min,
      max_price: globalPriceRange.max,
      min_rating: 0,
      max_rating: 5,
      min_reviews: '',
      max_reviews: '',
      sort: '-created_at',
      limit: 10
    }));
    setFilters(prev => ({
      ...prev,
      search: '',
      category: '',
      min_price: globalPriceRange.min,
      max_price: globalPriceRange.max,
      min_rating: 0,
      max_rating: 5,
      min_reviews: '',
      max_reviews: '',
      sort: '-created_at',
      limit: 10
    }));
    setSearchInput('');
  };

  if (loading && products.length === 0) {
    return <div className="loading">Загрузка товаров...</div>;
  }

  if (error) {
    return <div className="error">{error}</div>;
  }

  return (
    <div className="product-list">
      <div className="page-header">
        <h1>Товары</h1>
        <button onClick={clearFilters} className="btn btn-secondary">
          Очистить фильтры
        </button>
      </div>

      <div className="filters-section">
        <div className="filters">
          <h2>Фильтры</h2>
          
          {/* Поиск и категория */}
          <div className="filter-row">
            <input
              type="text"
              placeholder="Поиск товаров..."
              value={searchInput}
              onChange={e => setSearchInput(e.target.value)}
              onKeyDown={handleSearchKeyDown}
              className="filter-input"
            />
            <button
              className="btn btn-primary"
              style={{ marginLeft: 8 }}
              onClick={applySearch}
            >
              Применить
            </button>
            <select
              value={formState.category}
              onChange={e => handleFormChange('category', e.target.value)}
              className="filter-select"
            >
              <option value="">Все категории</option>
              {categories.map(cat => (
                <option key={cat} value={cat}>{cat}</option>
              ))}
            </select>
          </div>
          
          {/* Слайдеры */}
          <div className="filter-row">
            <div className="filter-group">
              <label>Диапазон цен:</label>
              <div className="slider-row">
                <input
                  type="range"
                  min={globalPriceRange.min}
                  max={formState.max_price || globalPriceRange.max}
                  value={formState.min_price || globalPriceRange.min}
                  step={1}
                  onChange={e => {
                    const min = Number(e.target.value);
                    setFormState(prev => ({
                      ...prev,
                      min_price: isNaN(min) ? globalPriceRange.min : (min > prev.max_price ? prev.max_price : min)
                    }));
                  }}
                  className="range-slider"
                />
                <input
                  type="range"
                  min={formState.min_price || globalPriceRange.min}
                  max={globalPriceRange.max}
                  value={formState.max_price || globalPriceRange.max}
                  step={1}
                  onChange={e => {
                    const max = Number(e.target.value);
                    setFormState(prev => ({
                      ...prev,
                      max_price: isNaN(max) ? globalPriceRange.max : (max < prev.min_price ? prev.min_price : max)
                    }));
                  }}
                  className="range-slider"
                />
              </div>
              <div className="slider-values">
                <span>{formState.min_price || globalPriceRange.min}₽</span>
                <span>{formState.max_price || globalPriceRange.max}₽</span>
              </div>
            </div>
            <div className="filter-group">
              <label>Диапазон рейтинга:</label>
              <div className="slider-row">
                <input
                  type="range"
                  min={0}
                  max={formState.max_rating || 5}
                  step={0.1}
                  value={formState.min_rating}
                  onChange={e => {
                    const min = Number(e.target.value);
                    setFormState(prev => ({ ...prev, min_rating: min > prev.max_rating ? prev.max_rating : min }));
                  }}
                  className="range-slider"
                />
                <input
                  type="range"
                  min={formState.min_rating || 0}
                  max={5}
                  step={0.1}
                  value={formState.max_rating}
                  onChange={e => {
                    const max = Number(e.target.value);
                    setFormState(prev => ({ ...prev, max_rating: max < prev.min_rating ? prev.min_rating : max }));
                  }}
                  className="range-slider"
                />
              </div>
              <div className="slider-values">
                <span>{formState.min_rating}</span>
                <span>{formState.max_rating}</span>
              </div>
            </div>
          </div>

          {/* Сортировка */}
          <div className="sort-controls">
            <span>Сортировка:</span>
            <button className={filters.sort === 'price' ? 'active' : ''} onClick={() => handleSortChange('price')}>Цена ↑</button>
            <button className={filters.sort === '-price' ? 'active' : ''} onClick={() => handleSortChange('-price')}>Цена ↓</button>
            <button className={filters.sort === 'rating' ? 'active' : ''} onClick={() => handleSortChange('rating')}>Рейтинг ↑</button>
            <button className={filters.sort === '-rating' ? 'active' : ''} onClick={() => handleSortChange('-rating')}>Рейтинг ↓</button>
            <button className={filters.sort === 'reviews_count' ? 'active' : ''} onClick={() => handleSortChange('reviews_count')}>Отзывы ↑</button>
            <button className={filters.sort === '-reviews_count' ? 'active' : ''} onClick={() => handleSortChange('-reviews_count')}>Отзывы ↓</button>
            <button className={filters.sort === 'name' ? 'active' : ''} onClick={() => handleSortChange('name')}>Название ↑</button>
            <button className={filters.sort === '-name' ? 'active' : ''} onClick={() => handleSortChange('-name')}>Название ↓</button>
          </div>
        </div>
      </div>

      {/* Диаграммы */}
      <div className="charts-section">
        <div className="chart-container">
          <h3>Распределение цен</h3>
          <ResponsiveContainer width="100%" height={250}>
            <BarChart data={chartsData.priceHistogram}>
              <XAxis dataKey="range" />
              <YAxis />
              <Tooltip />
              <Bar dataKey="count" fill="#667eea" />
            </BarChart>
          </ResponsiveContainer>
        </div>
        <div className="chart-container">
          <h3>Скидка по рейтингам</h3>
          <ResponsiveContainer width="100%" height={250}>
            <LineChart data={chartsData.discountVsRating}>
              <XAxis dataKey="rating_range" />
              <YAxis />
              <Tooltip />
              <CartesianGrid stroke="#eee" strokeDasharray="5 5" />
              <Line type="monotone" dataKey="average_discount" stroke="#ff6b6b" />
              <Legend />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* Список товаров */}
      <div className="products-section">
        <div className="products-header">
          <h2>Товары ({pagination.count})</h2>
        </div>

        <div className="products-grid">
          {products.map(product => (
            <div key={product.id} className="product-card">
              <div className="product-info">
                <h3 className="product-name">{product.name}</h3>
                <div className="product-price">
                  {product.discount_price && product.discount_price < product.price ? (
                    <>
                      <span className="old-price">{product.price}₽</span>
                      <span className="current-price">{product.discount_price}₽</span>
                    </>
                  ) : (
                    <span className="current-price">{product.price}₽</span>
                  )}
                </div>
                {product.rating && (
                  <div className="product-rating">
                    ⭐ {product.rating} ({product.reviews_count || 0} отзывов)
                  </div>
                )}
                <div className="product-category">{product.category}</div>
                <Link to={`/products/${product.id}`} className="btn btn-primary">
                  Подробнее
                </Link>
              </div>
            </div>
          ))}
        </div>
        {products.length === 0 && <div className="no-products">Товары не найдены</div>}

        <div className="pagination">
          {pagination.previous && (
            <button
              onClick={() => {
                const url = new URL(pagination.previous, window.location.origin);
                const page = url.searchParams.get('page') || 1;
                navigate(`/products?page=${page}`);
              }}
            >
              ← Назад
            </button>
          )}
          <span>Всего товаров: {pagination.count}</span>
          {pagination.next && (
            <button
              onClick={() => {
                const url = new URL(pagination.next, window.location.origin);
                const page = url.searchParams.get('page') || 1;
                navigate(`/products?page=${page}`);
              }}
            >
              Вперед →
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

export default ProductList; 