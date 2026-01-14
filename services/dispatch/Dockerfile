FROM python:3.12-slim
WORKDIR /usr/src/app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY src/ ./

# Create a non-root user
RUN useradd -r appuser && chown -R appuser /usr/src/app
USER appuser

CMD ["python", "./app.py"]