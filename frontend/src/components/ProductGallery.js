import React, { useState, useEffect } from 'react';
import './ProductGallery.css';

const ProductGallery = ({ images = [], mainImage = '' }) => {
  const [currentImage, setCurrentImage] = useState(mainImage);
  const [galleryImages, setGalleryImages] = useState([]);
  const [showLightbox, setShowLightbox] = useState(false);
  
  useEffect(() => {
    // Фильтруем и сортируем изображения
    const filteredImages = images.filter(img => 
      img.size === 'big' || img.size === 'original' || img.size === '516x688'
    ).sort((a, b) => {
      // Сортируем: main image first, then by size
      if (a.is_main) return -1;
      if (b.is_main) return 1;
      return 0;
    });

    // Если есть главное изображение, добавляем его первым
    const resultImages = mainImage && !filteredImages.some(img => img.url === mainImage)
      ? [{ url: mainImage, is_main: true, size: 'big' }, ...filteredImages]
      : filteredImages;

    setGalleryImages(resultImages);
    
    // Устанавливаем первое изображение как текущее
    if (resultImages.length > 0 && !currentImage) {
      setCurrentImage(resultImages[0].url);
    }
  }, [images, mainImage]);
  
  if (galleryImages.length === 0) {
    return (
      <div className="product-gallery">
        <div className="no-images">Нет изображений товара</div>
      </div>
    );
  }

  return (
    <div className="product-gallery">
      <div className="main-image-container">
        {currentImage && (
          <img 
            src={currentImage} 
            alt="Product" 
            className="main-image"
            onClick={() => galleryImages.length > 1 && setShowLightbox(true)}
          />
        )}
      </div>
      
      {galleryImages.length > 1 && (
        <div className="thumbnail-container">
          {galleryImages.map((img, index) => (
            <div 
              key={index} 
              className={`thumbnail ${currentImage === img.url ? 'active' : ''}`}
              onClick={() => setCurrentImage(img.url)}
            >
              <img 
                src={img.url} 
                alt={`Thumbnail ${index + 1}`} 
                className="thumbnail-image"
                loading="lazy"
              />
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

export default ProductGallery;