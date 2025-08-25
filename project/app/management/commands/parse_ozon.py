from django.core.management.base import BaseCommand
from app.unified_parser import UnifiedParser
import asyncio

class Command(BaseCommand):
    help = 'Parse products from Ozon'

    def add_arguments(self, parser):
        parser.add_argument('query', type=str, help='Search query')
        parser.add_argument('--limit', type=int, default=10, help='Limit results')

    def handle(self, *args, **options):
        async def run_parsing():
            parser = UnifiedParser()
            try:
                results = await parser.search_products(
                    options['query'], 
                    marketplace='ozon', 
                    limit=options['limit']
                )
                
                for product in results.get('ozon', []):
                    self.stdout.write(
                        f"{product.name} - {product.price} руб. "
                        f"(Артикул: {product.article})"
                    )
                    
            finally:
                await parser.close_sessions()

        asyncio.run(run_parsing())