# management/commands/user_stats.py
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db import models
from django.db.models import Count, Avg, Sum, Max
from app.models import TelegramUser, UserSearchHistory

class Command(BaseCommand):
    help = 'Статистика пользователей бота'
    
    def add_arguments(self, parser):
        parser.add_argument(
            '--detailed',
            action='store_true',
            help='Показать подробную статистику',
        )
    
    def handle(self, *args, **options):
        detailed = options['detailed']
        
        # Базовая статистика
        total_users = TelegramUser.objects.count()
        
        # Активные пользователи (за последние 7 дней)
        active_users = TelegramUser.objects.filter(
            last_activity__gte=timezone.now() - timezone.timedelta(days=7)
        ).count()
        
        # Очень активные пользователи (за последние 24 часа)
        very_active_users = TelegramUser.objects.filter(
            last_activity__gte=timezone.now() - timezone.timedelta(hours=24)
        ).count()
        
        # Статистика поисков
        search_stats = TelegramUser.objects.aggregate(
            total_searches=Sum('search_count'),
            avg_searches=Avg('search_count'),
            max_searches=Max('search_count'),
            users_with_searches=Count('id', filter=models.Q(search_count__gt=0))
        )
        
        # Статистика по премиум пользователям
        premium_stats = TelegramUser.objects.filter(is_premium=True).aggregate(
            count=Count('id'),
            avg_searches=Avg('search_count')
        )
        
        # Вывод базовой статистики
        self.stdout.write(
            self.style.SUCCESS(
                "📊 ОСНОВНАЯ СТАТИСТИКА ПОЛЬЗОВАТЕЛЕЙ\n"
                "─────────────────────────────────\n"
                f"• 👥 Всего пользователей: {total_users}\n"
                f"• 🎯 Активных за неделю: {active_users}\n"
                f"• ⚡ Активных за 24 часа: {very_active_users}\n"
                f"• 🔍 Всего поисков: {search_stats['total_searches'] or 0}\n"
                f"• 📈 Пользователей с поисками: {search_stats['users_with_searches']}\n"
                f"• 📊 Среднее количество поисков: {search_stats['avg_searches'] or 0:.1f}\n"
                f"• 🏆 Максимум поисков: {search_stats['max_searches'] or 0}\n"
            )
        )
        
        # Подробная статистика
        if detailed:
            self.stdout.write(
                self.style.WARNING(
                    "\n📈 ПОДРОБНАЯ СТАТИСТИКА\n"
                    "──────────────────────\n"
                )
            )
            
            # Топ 10 самых активных пользователей
            top_users = TelegramUser.objects.order_by('-search_count')[:10]
            
            self.stdout.write("🏆 ТОП-10 самых активных пользователей:")
            for i, user in enumerate(top_users, 1):
                self.stdout.write(
                    f"  {i}. {user.first_name} ({user.user_id}): "
                    f"{user.search_count} поисков, "
                    f"последняя активность: {user.last_activity.strftime('%d.%m.%Y')}"
                )
            
            # Статистика по дням
            from django.db.models.functions import TruncDay
            daily_stats = UserSearchHistory.objects.annotate(
                day=TruncDay('created_at')
            ).values('day').annotate(
                searches=Count('id'),
                users=Count('user', distinct=True)
            ).order_by('-day')[:7]
            
            self.stdout.write("\n📅 СТАТИСТИКА ЗА ПОСЛЕДНИЕ 7 ДНЕЙ:")
            for stat in daily_stats:
                self.stdout.write(
                    f"  {stat['day'].strftime('%d.%m.%Y')}: "
                    f"{stat['searches']} поисков, {stat['users']} пользователей"
                )