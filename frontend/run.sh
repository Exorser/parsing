#!/bin/bash

# Остановить выполнение при ошибке
set -e

echo "Собираем фронтенд (npm run build)..."
npm run build

echo "Копируем build в ../project/app/static/app/ ..."
cp -r build/* ../project/app/static/app/

echo "Собираем статику Django..."
cd ../project
python manage.py collectstatic --noinput

echo "Готово! Фронтенд собран, скопирован и статика обновлена."