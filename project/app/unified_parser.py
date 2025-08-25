# from typing import List, Dict
# from .models import Product, Marketplace
# from .wildberries_parser import WildberriesParser
# from .ozon_parser import OzonParser

# class UnifiedParser:
#     def __init__(self):
#         self.wb_parser = WildberriesParser()
#         self.ozon_parser = OzonParser()

#     def search_products(self, query: str, marketplace: str = 'both', limit: int = 50) -> Dict[str, List[Product]]:
#         """Синхронный метод поиска"""
#         results = {}
        
#         if marketplace in ['wb', 'both']:
#             wb_products = self.wb_parser.search_products(query, limit)
#             results['wildberries'] = wb_products
        
#         if marketplace in ['ozon', 'both']:
#             ozon_products = self.ozon_parser.search_products(query, limit)
#             results['ozon'] = ozon_products
        
#         return results

#     def close_sessions(self):
#         """Закрытие всех сессий"""
#         self.wb_parser.close_session()
#         self.ozon_parser.close_session()