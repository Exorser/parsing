import React, { useState, useEffect } from 'react';
import { useParams, Link } from 'react-router-dom';
import axios from 'axios';
import './ProductDetail.css';
import PropTypes from 'prop-types';

/**
 * Детальная страница товара Wildberries
 * @component
 */
function ProductDetail() {
  const { id } = useParams();
  const [product, setProduct] = useState(null);
  const [similarProducts, setSimilarProducts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [selectedImage, setSelectedImage] = useState(null);
  const [imageErrors, setImageErrors] = useState({});

  useEffect(() => {
    fetchProduct();
  }, [id]);

  const fetchProduct = async () => {
    try {
      setLoading(true);
      const response = await axios.get(`/api/products/${id}/`);
      setProduct(response.data.product);
      setSimilarProducts(response.data.similar_products);
      
      // Устанавливаем первое изображение как выбранное по умолчанию
      if (response.data.product.images && response.data.product.images.length > 0) {
        setSelectedImage(response.data.product.images[0].image_url);
      } else if (response.data.product.image_url) {
        setSelectedImage(response.data.product.image_url);
      }
    } catch (err) {
      setError('Товар не найден');
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  const handleImageError = (url) => {
  setImageErrors(prev => ({ ...prev, [url]: true }));
};

  if (loading) {
    return <div className="loading">Загрузка товара...</div>;
  }

  if (error || !product) {
    return (
      <div className="error">
        <p>{error}</p>
        <Link to="/products" className="btn btn-primary">Вернуться к списку</Link>
      </div>
    );
  }

  const actualPrice = product.discount_price && product.discount_price < product.price 
    ? product.discount_price 
    : product.price;

  const discountPercent = product.discount_price && product.discount_price < product.price
    ? Math.round(((product.price - product.discount_price) / product.price) * 100)
    : 0;

  // Получаем все изображения товара
  const allImages = product.images && product.images.length > 0 
    ? product.images.map(img => img.image_url) 
    : product.image_url 
      ? [product.image_url] 
      : [];

  return (
    <div className="product-detail">
      <div className="breadcrumb">
        <Link to="/">Главная</Link> / 
        <Link to="/products">Товары</Link> / 
        <span>{product.name}</span>
      </div>

      <div className="product-main">
        <div className="product-images">
          {/* Основное изображение */}
          {selectedImage && (
            <div className="main-image-container">
              <img src={selectedImage} alt={product.name} className="main-image" />
            </div>
          )}
          
          {/* Галерея миниатюр */}
          {allImages.length > 1 && (
            <div className="thumbnail-gallery">
              {allImages.map((imgUrl, index) => (
                <div 
                  key={index} 
                  className={`thumbnail-container ${selectedImage === imgUrl ? 'active' : ''}`}
                  onClick={() => setSelectedImage(imgUrl)}
                >
                  <img 
                    src={imgUrl} 
                    alt={`${product.name} - фото ${index + 1}`} 
                    className="thumbnail"
                  />
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="product-info">
          <h1 className="product-title">{product.name}</h1>
          
          <div className="product-price-section">
            {product.discount_price && product.discount_price < product.price ? (
              <div className="price-with-discount">
                <span className="old-price">{product.price}₽</span>
                <span className="current-price">{product.discount_price}₽</span>
                <span className="discount-badge">-{discountPercent}%</span>
              </div>
            ) : (
              <span className="current-price">{product.price}₽</span>
            )}
          </div>

          {product.rating && (
            <div className="product-rating">
              <div className="stars">
                {'⭐'.repeat(Math.floor(product.rating))}
                {product.rating % 1 !== 0 && '⭐'}
              </div>
              <span className="rating-text">
                {product.rating} ({product.reviews_count || 0} отзывов)
              </span>
            </div>
          )}

          <div className="product-meta">
            <div className="meta-item">
              <strong>Категория:</strong> {product.category}
            </div>
            <div className="meta-item">
              <strong>Поисковый запрос:</strong> {product.search_query}
            </div>
            <div className="meta-item">
              <strong>Дата добавления:</strong> {new Date(product.created_at).toLocaleDateString()}
            </div>
          </div>

          <div className="product-actions">
            {product.product_url && (
              <a 
                href={product.product_url} 
                target="_blank" 
                rel="noopener noreferrer" 
                className="btn btn-primary"
              >
                Открыть на Wildberries
              </a>
            )}
            <Link to="/products" className="btn btn-secondary">
              Назад к списку
            </Link>
          </div>
        </div>
      </div>

      {similarProducts.length > 0 && (
        <div className="similar-products">
          <h2>Похожие товары</h2>
          <div className="similar-grid">
            {similarProducts.map(similar => (
              <div key={similar.id} className="similar-card">
                {similar.image_url && (
                  <img 
                    src={similar.image_url} 
                    alt={similar.name} 
                    className="similar-image"
                  />
                )}
                <div className="similar-info">
                  <h3>{similar.name}</h3>
                  <div className="similar-price">
                    {similar.discount_price && similar.discount_price < similar.price ? (
                      <>
                        <span className="old-price">{similar.price}₽</span>
                        <span className="current-price">{similar.discount_price}₽</span>
                      </>
                    ) : (
                      <span className="current-price">{similar.price}₽</span>
                    )}
                  </div>
                  <Link to={`/products/${similar.id}`} className="btn btn-small">
                    Подробнее
                  </Link>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

ProductDetail.propTypes = {
  // Добавьте propTypes по необходимости
};

export default ProductDetail;