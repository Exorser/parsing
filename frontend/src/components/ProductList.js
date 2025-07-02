import React, { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import axios from 'axios';
import './ProductList.css';

function ProductList() {
  console.log('ProductList loaded!');
  const [filters, setFilters] = useState({
    search: '',
    category: '',
    min_price: '',
    max_price: '',
    min_rating: '',
    min_reviews: '',
    sort: '-created_at'
  });
  const [formState, setFormState] = useState({
    search: '',
    category: '',
    min_price: '',
    max_price: '',
    min_rating: '',
    min_reviews: '',
  });
  const [products, setProducts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [categories, setCategories] = useState([]);
  const [pagination, setPagination] = useState({
    count: 0,
    next: null,
    previous: null
  });
  const [priceRange, setPriceRange] = useState({ min: 0, max: 100000 });
  const [chartsData, setChartsData] = useState({
    priceHistogram: [],
    discountVsRating: []
  });
  const [page, setPage] = useState(1);

  useEffect(() => {
    setProducts([]);
    setLoading(true);
    const timer = setTimeout(() => {
      fetchProducts();
      fetchChartsData();
    }, 250);
    return () => clearTimeout(timer);
  }, [filters, page]);

  const fetchProducts = async () => {
    try {
      const params = new URLSearchParams();
      Object.keys(filters).forEach(key => {
        if (filters[key]) {
          params.append(key, filters[key]);
        }
      });
      params.append('page', page);
      const response = await axios.get(`/api/products/?${params}`);
      setProducts(response.data.results);
      setPagination({
        count: response.data.count,
        next: response.data.next,
        previous: response.data.previous
      });
      setCategories(response.data.categories || []);
      if (response.data.price_range) {
        setPriceRange(response.data.price_range);
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
        if (filters[key] && key !== 'sort') {
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

  const handleChange = (key, value) => {
    setFormState(prev => ({ ...prev, [key]: value }));
  };

  const applyFilters = () => {
    setFilters({ ...formState, sort: filters.sort });
  };

  const handleSortChange = (sort) => {
    setFilters(prev => ({ ...prev, sort }));
  };

  const clearFilters = () => {
    setFilters({
      search: '',
      category: '',
      min_price: '',
      max_price: '',
      min_rating: '',
      min_reviews: '',
      sort: '-created_at'
    });
    setFormState({
      search: '',
      category: '',
      min_price: '',
      max_price: '',
      min_rating: '',
      min_reviews: '',
    });
  };

  // Обработчик для предотвращения отправки формы при нажатии Enter
  const handleKeyDown = (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      applyFilters();
    }
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
        <button onClick={clearFilters} className="btn btn-secondary" type="button">
          Очистить фильтры
        </button>
      </div>
      <div className="filters-section">
        <div className="filters">
          <h2>Фильтры</h2>
          <div className="filter-row">
            <input
              type="text"
              placeholder="Поиск товаров..."
              value={formState.search}
              onChange={e => handleChange('search', e.target.value)}
              onKeyDown={handleKeyDown}
              className="filter-input"
              autoComplete="off"
            />
            <select
              value={formState.category}
              onChange={e => handleChange('category', e.target.value)}
              className="filter-select"
            >
              <option value="">Все категории</option>
              {categories.map(cat => (
                <option key={cat} value={cat}>{cat}</option>
              ))}
            </select>
          </div>
          <div className="filter-group">
            <h3>Диапазон цен</h3>
            <div className="price-slider-container">
              <div className="price-inputs">
                <input
                  type="number"
                  placeholder="Мин. цена"
                  value={formState.min_price}
                  onChange={e => handleChange('min_price', e.target.value)}
                  onKeyDown={handleKeyDown}
                  className="price-input"
                  autoComplete="off"
                />
                <span>-</span>
                <input
                  type="number"
                  placeholder="Макс. цена"
                  value={formState.max_price}
                  onChange={e => handleChange('max_price', e.target.value)}
                  onKeyDown={handleKeyDown}
                  className="price-input"
                  autoComplete="off"
                />
              </div>
              <div className="price-range">
                <span>{formState.min_price || priceRange.min}₽</span>
                <span>{formState.max_price || priceRange.max}₽</span>
              </div>
            </div>
          </div>
          <div className="filter-row">
            <div className="filter-group">
              <label>Мин. рейтинг:</label>
              <input
                type="number"
                min="0"
                max="5"
                step="0.1"
                value={formState.min_rating}
                onChange={e => handleChange('min_rating', e.target.value)}
                onKeyDown={handleKeyDown}
                className="filter-input"
                autoComplete="off"
              />
            </div>
            <div className="filter-group">
              <label>Мин. отзывов:</label>
              <input
                type="number"
                min="0"
                value={formState.min_reviews}
                onChange={e => handleChange('min_reviews', e.target.value)}
                onKeyDown={handleKeyDown}
                className="filter-input"
                autoComplete="off"
              />
            </div>
          </div>
          <div style={{ marginTop: '1rem', textAlign: 'right' }}>
            <button type="button" className="btn btn-primary" onClick={applyFilters}>
              Применить фильтры
            </button>
          </div>
          <div className="sort-controls">
            <span>Сортировка:</span>
            <button
              className={filters.sort === 'price' ? 'active' : ''}
              onClick={() => handleSortChange('price')}
              type="button"
            >
              Цена ↑
            </button>
            <button
              className={filters.sort === '-price' ? 'active' : ''}
              onClick={() => handleSortChange('-price')}
              type="button"
            >
              Цена ↓
            </button>
            <button
              className={filters.sort === 'rating' ? 'active' : ''}
              onClick={() => handleSortChange('rating')}
              type="button"
            >
              Рейтинг ↑
            </button>
            <button
              className={filters.sort === '-rating' ? 'active' : ''}
              onClick={() => handleSortChange('-rating')}
              type="button"
            >
              Рейтинг ↓
            </button>
            <button
              className={filters.sort === 'reviews_count' ? 'active' : ''}
              onClick={() => handleSortChange('reviews_count')}
              type="button"
            >
              Отзывы ↑
            </button>
            <button
              className={filters.sort === '-reviews_count' ? 'active' : ''}
              onClick={() => handleSortChange('-reviews_count')}
              type="button"
            >
              Отзывы ↓
            </button>
            <button
              className={filters.sort === 'name' ? 'active' : ''}
              onClick={() => handleSortChange('name')}
              type="button"
            >
              Название ↑
            </button>
            <button
              className={filters.sort === '-name' ? 'active' : ''}
              onClick={() => handleSortChange('-name')}
              type="button"
            >
              Название ↓
            </button>
          </div>
        </div>
      </div>
      {/* Диаграммы */}
      <div className="charts-section">
        <div className="chart-container">
          <h3>Распределение цен</h3>
          <div className="price-histogram">
            {chartsData.priceHistogram.map((item, index) => (
              <div key={index} className="histogram-bar">
                <div 
                  className="bar" 
                  style={{ 
                    height: `${Math.max((item.count / Math.max(...chartsData.priceHistogram.map(h => h.count))) * 200, 20)}px` 
                  }}
                >
                  <span className="bar-count">{item.count}</span>
                </div>
                <span className="bar-label">{item.range}</span>
              </div>
            ))}
          </div>
        </div>
        <div className="chart-container">
          <h3>Скидки по рейтингам</h3>
          <div className="discount-chart">
            {chartsData.discountVsRating.map((item, index) => (
              <div key={index} className="discount-bar">
                <div 
                  className="bar" 
                  style={{ 
                    height: `${Math.max((item.average_discount / Math.max(...chartsData.discountVsRating.map(d => d.average_discount))) * 200, 20)}px` 
                  }}
                >
                  <span className="bar-value">{item.average_discount}%</span>
                </div>
                <span className="bar-label">Рейтинг {item.rating_range}</span>
              </div>
            ))}
          </div>
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
              {product.image_url && (
                <img src={product.image_url} alt={product.name} className="product-image" />
              )}
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
        {products.length === 0 && (
          <div className="no-products">
            <p>Товары не найдены</p>
          </div>
        )}
        <div className="pagination">
          <button
            type="button"
            onClick={() => setPage(page > 1 ? page - 1 : 1)}
            disabled={page === 1}
          >
            ← Назад
          </button>
          <span>Страница: {page} / {Math.ceil(pagination.count / products.length || 1)}</span>
          <button
            type="button"
            onClick={() => setPage(page + 1)}
            disabled={!pagination.next}
          >
            Вперед →
          </button>
          <span>Всего товаров: {pagination.count}</span>
        </div>
      </div>
    </div>
  );
}

export default ProductList;