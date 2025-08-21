import React, { useState, useEffect } from 'react';
import { useParams, Link } from 'react-router-dom';
import axios from 'axios';
import './ProductDetail.css';
import ProductGallery from './ProductGallery';

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

  useEffect(() => {
    const fetchProduct = async () => {
      try {
        setLoading(true);
        const response = await axios.get(`/api/products/${id}/`);
        const productData = response.data.product;
        
        // Формируем данные для галереи
        const galleryImages = productData.images || [];
        const mainImage = productData.image_url;
        
        setProduct({
          ...productData,
          galleryImages,
          mainImage
        });
        
        setSimilarProducts(response.data.similar_products || []);
      } catch (err) {
        setError('Товар не найден');
        console.error('Ошибка загрузки товара:', err);
      } finally {
        setLoading(false);
      }
    };

    fetchProduct();
  }, [id]);

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

  const discountPercent = product.discount_price && product.discount_price < product.price
    ? Math.round(((product.price - product.discount_price) / product.price) * 100)
    : 0;

  return (
    <div className="product-detail">
      <div className="breadcrumb">
        <Link to="/">Главная</Link> / 
        <Link to="/products">Товары</Link> / 
        <span>{product.name}</span>
      </div>

      <div className="product-main">
        <ProductGallery 
          images={product.galleryImages} 
          mainImage={product.mainImage} 
        /> 
        <div className="product-info">
          <h1 className="product-title">{product.name}</h1>
          <p className={`availability ${product.availability_class}`}>
              {product.availability_status}
              {product.quantity > 0 && ` (${product.quantity} шт.)`}
          </p>
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

export default ProductDetail;