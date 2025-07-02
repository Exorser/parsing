import React from 'react';
import { BrowserRouter as Router, Routes, Route, Link } from 'react-router-dom';
import './App.css';
import ProductList from './components/ProductList';
import ProductDetail from './components/ProductDetail';
import SearchForm from './components/SearchForm';

function App() {
  return (
    <Router>
      <div className="App">
        <nav className="navbar">
          <div className="nav-container">
            <Link to="/" className="nav-logo">
              Wildberries Parser
            </Link>
            <ul className="nav-menu">
              <li className="nav-item">
                <Link to="/" className="nav-link">Главная</Link>
              </li>
              <li className="nav-item">
                <Link to="/products" className="nav-link">Товары</Link>
              </li>
              <li className="nav-item">
                <Link to="/search" className="nav-link">Поиск</Link>
              </li>
            </ul>
          </div>
        </nav>

        <main className="main-content">
          <Routes>
            <Route path="/" element={<Home />} />
            <Route path="/products" element={<ProductList />} />
            <Route path="/products/:id" element={<ProductDetail />} />
            <Route path="/search" element={<SearchForm />} />
          </Routes>
        </main>
      </div>
    </Router>
  );
}

function Home() {
  return (
    <div className="home">
      <div className="hero">
        <h1>Wildberries Parser</h1>
        <p>Анализ товаров и цен с Wildberries</p>
        <div className="hero-buttons">
          <Link to="/products" className="btn btn-primary">Смотреть товары</Link>
          <Link to="/search" className="btn btn-secondary">Начать поиск</Link>
        </div>
      </div>
      
      <div className="features">
        <div className="feature-card">
          <h3>📊 Аналитика</h3>
          <p>Фильтры, сортировка и диаграммы</p>
        </div>
        <div className="feature-card">
          <h3>🔍 Поиск</h3>
          <p>Парсинг товаров по запросу</p>
        </div>
        <div className="feature-card">
          <h3>📈 Статистика</h3>
          <p>Графики и диаграммы в реальном времени</p>
        </div>
      </div>
    </div>
  );
}

export default App;
