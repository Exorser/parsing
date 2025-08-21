from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from app.parser import WildberriesParser

class Command(BaseCommand):
    help = 'Парсинг товаров с Wildberries по заданному запросу'

    def add_arguments(self, parser):
        parser.add_argument(
            'query',
            type=str,
            help='Поисковый запрос для парсинга'
        )
        parser.add_argument(
            '--category',
            type=str,
            default='',
            help='Категория товаров (опционально)'
        )
        parser.add_argument(
            '--limit',
            type=int,
            default=10,
            help='Количество товаров для парсинга (по умолчанию 10)'
        )

    def handle(self, *args, **options):
        query = options['query']
        category = options['category']
        limit = options['limit']

        self.stdout.write(
            self.style.SUCCESS(f'Начинаем парсинг Wildberries для запроса: "{query}"')
        )
        
        if category:
            self.stdout.write(f'Категория: {category}')
        
        self.stdout.write(f'Лимит товаров: {limit}')

        parser = WildberriesParser()
        
        try:
            saved_count = parser.parse_and_save(query, category, limit)

            if saved_count == 0:
                self.stdout.write(
                    self.style.WARNING('Товары не найдены или не сохранены')
                )
                return

            self.stdout.write(
                self.style.SUCCESS(f'Успешно сохранено товаров: {saved_count}')
            )
            
        except Exception as e:
            raise CommandError(f'Ошибка при парсинге: {e}')