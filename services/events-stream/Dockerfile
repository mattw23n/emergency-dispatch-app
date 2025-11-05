FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/

# Create a non-root user
RUN useradd -r appuser && chown -R appuser /app
USER appuser

CMD ["python", "src/app.py"]
