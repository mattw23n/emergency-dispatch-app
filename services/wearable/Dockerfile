FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

# Create a non-root user
RUN useradd -r -u 1000 appuser && chown -R appuser /app
USER appuser

EXPOSE 5000
CMD ["python", "src/app.py"]
