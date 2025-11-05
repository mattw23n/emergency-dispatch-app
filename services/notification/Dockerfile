FROM python:3.12-slim
WORKDIR /usr/src/app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY src/ ./

# Create a non-root user
RUN useradd -r -u 1000 appuser && chown -R appuser /app
USER appuser

CMD ["python", "./app.py"]
