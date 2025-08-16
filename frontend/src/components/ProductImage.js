import React, {useState, useEffect} from "react";
import './ProductImage.css'
function ProductImages({ product }) {
  const [mainImage, setMainImage] = useState('');
  const [allImages, setAllImages] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const loadImages = async () => {
      try {
        // Если есть основное изображение, добавляем его первым
        const images = product.image_url ? [product.image_url] : [];
        
        // Добавляем все дополнительные изображения
        if (product.images && product.images.length > 0) {
          images.push(...product.images.map(img => img.image_url));
        }
        
        // Проверяем доступность изображений
        const availableImages = [];
        for (const url of images) {
          if (await checkImageExists(url)) {
            availableImages.push(url);
          }
        }
        
        setAllImages(availableImages);
        setMainImage(availableImages[0] || '/placeholder.jpg');
      } catch (error) {
        console.error('Error loading images:', error);
        setMainImage('/placeholder.jpg');
      } finally {
        setLoading(false);
      }
    };
    
    loadImages();
  }, [product]);

  const checkImageExists = (url) => {
    return new Promise((resolve) => {
      const img = new Image();
      img.onload = () => resolve(true);
      img.onerror = () => resolve(false);
      img.src = url;
    });
  };

  if (loading) {
    return <div className="image-loader">Загрузка изображений...</div>;
  }

  return (
    <div className="product-images-container">
      <div className="main-image-wrapper">
        <img 
          src={mainImage} 
          alt={product.name}
          onError={(e) => {
            e.target.src = '/placeholder.jpg';
          }}
          className="main-product-image"
        />
      </div>
      
      {allImages.length > 1 && (
        <div className="thumbnails">
          {allImages.map((img, index) => (
            <img
              key={index}
              src={img}
              alt={`${product.name} ${index + 1}`}
              onClick={() => setMainImage(img)}
              onError={(e) => {
                e.target.src = '/placeholder-small.jpg';
              }}
              className={`thumbnail ${mainImage === img ? 'active' : ''}`}
            />
          ))}
        </div>
      )}
    </div>
  );
}

export default ProductImages