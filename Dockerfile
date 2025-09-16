FROM python:3.12-slim

WORKDIR /app

# зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# приложение
COPY app.py .
COPY zoom_client.py .
COPY telemost_client.py .
COPY yandex_calendar.py .
COPY static ./static

ENV PYTHONUNBUFFERED=1
EXPOSE 8080

CMD ["python", "app.py"]
