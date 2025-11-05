FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /usr/src/app
RUN pip install --no-cache-dir --upgrade pip
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY src/ ./
EXPOSE 8011

# Create a non-root user
RUN useradd -r appuser && chown -R appuser /usr/src/app
USER appuser

CMD ["sh","-c","gunicorn -w 1 -b 0.0.0.0:${PORT:-8011} app:app"]
