FROM python:3.12-slim
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем всё нужное приложение
COPY telemost_client.py .
COPY app.py .
COPY zoom_client.py .
COPY static ./static

ENV PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["python", "app.py"]
