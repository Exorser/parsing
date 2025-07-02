import React, { useState } from 'react';
import axios from 'axios';
import './SearchForm.css';

function SearchForm() {
  const [searchQuery, setSearchQuery] = useState('');
  const [category, setCategory] = useState('');
  const [limit, setLimit] = useState(10);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');

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
        category: category || null,
        limit: limit
      });

      setMessage('Парсинг запущен успешно! Товары будут добавлены в базу данных.');
      setSearchQuery('');
      setCategory('');
      setLimit(10);
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
          <input
            type="text"
            value={category}
            onChange={e => setCategory(e.target.value)}
            placeholder="Категория (опционально)"
            className="search-input"
            disabled={loading}
            style={{ width: '200px', marginLeft: 8 }}
          />
          <select
            value={limit}
            onChange={e => setLimit(Number(e.target.value))}
            className="search-select"
            disabled={loading}
            style={{ width: '120px', minWidth: 120, marginLeft: 8 }}
          >
            <option value={10}>10</option>
            <option value={50}>50</option>
            <option value={100}>100</option>
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