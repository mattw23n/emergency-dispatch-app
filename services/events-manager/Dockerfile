FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /usr/src/app
RUN pip install --no-cache-dir --upgrade pip
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY src/ ./
EXPOSE 8011
CMD ["sh","-c","gunicorn -w 1 -b 0.0.0.0:${PORT:-8011} app:app"]
