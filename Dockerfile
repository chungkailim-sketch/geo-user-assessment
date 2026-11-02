FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY templates ./templates
COPY static ./static
RUN useradd --create-home appuser && mkdir -p /app/data && chown appuser /app/data
USER appuser
EXPOSE 8000
# Hosts such as Railway inject $PORT; default to 8000 locally.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
