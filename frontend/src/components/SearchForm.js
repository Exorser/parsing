import React, { useState, useEffect } from 'react';
import axios from 'axios';
import './SearchForm.css';

function SearchForm() {
  const [searchQuery, setSearchQuery] = useState('');
  const [category, setCategory] = useState('');
  const [categories, setCategories] = useState([]);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');

  useEffect(() => {
    fetchCategories();
  }, []);

  const fetchCategories = async () => {
    try {
      const response = await axios.get('/api/products/');
      const categories = response.data.categories || [];
      setCategories(categories);
    } catch (err) {
      console.error('Ошибка загрузки категорий:', err);
    }
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    
    if (!searchQuery.trim()) {
      setError('Введите поисковый запрос');
      return;
    }

    try {
      setLoading(true);
      setError('');
      setMessage('');

      const response = await axios.post('/api/start-parsing/', {
        search_query: searchQuery.trim(),
        category: category || null
      });

      setMessage('Парсинг запущен успешно! Товары будут добавлены в базу данных.');
      setSearchQuery('');
      setCategory('');
    } catch (err) {
      setError(err.response?.data?.error || 'Ошибка при запуске парсинга');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="search-form">
      <div className="search-header">
        <h1>Поиск товаров</h1>
        <p>Введите поисковый запрос для парсинга товаров с Wildberries</p>
      </div>

      <form onSubmit={handleSubmit} className="search-container">
        <div className="search-input-group">
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Например: iPhone, ноутбук, кроссовки..."
            className="search-input"
            disabled={loading}
          />
          <select
            value={category}
            onChange={(e) => setCategory(e.target.value)}
            className="search-select"
            disabled={loading}
          >
            <option value="">Все категории (опционально)</option>
            {categories.map(cat => (
              <option key={cat} value={cat}>{cat}</option>
            ))}
          </select>
          <button 
            type="submit" 
            className="search-button"
            disabled={loading}
          >
            {loading ? 'Парсинг...' : 'Начать поиск'}
          </button>
        </div>

        {error && (
          <div className="error-message">
            {error}
          </div>
        )}

        {message && (
          <div className="success-message">
            {message}
          </div>
        )}
      </form>

      <div className="search-tips">
        <h3>Советы по поиску:</h3>
        <ul>
          <li>Используйте конкретные названия товаров</li>
          <li>Добавляйте бренды для более точного поиска</li>
          <li>Выберите категорию для более релевантных результатов</li>
          <li>Парсинг может занять некоторое время</li>
        </ul>
      </div>

      <div className="recent-searches">
        <h3>Примеры запросов:</h3>
        <div className="search-examples">
          <button 
            onClick={() => setSearchQuery('iPhone 15')}
            className="example-button"
          >
            iPhone 15
          </button>
          <button 
            onClick={() => setSearchQuery('MacBook Pro')}
            className="example-button"
          >
            MacBook Pro
          </button>
          <button 
            onClick={() => setSearchQuery('Nike кроссовки')}
            className="example-button"
          >
            Nike кроссовки
          </button>
          <button 
            onClick={() => setSearchQuery('Samsung Galaxy')}
            className="example-button"
          >
            Samsung Galaxy
          </button>
        </div>
      </div>
    </div>
  );
}

export default SearchForm; 