FROM python:3.11-slim

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y \
    curl \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN python -c "import nltk; nltk.download('punkt'); nltk.download('stopwords'); nltk.download('wordnet')"

COPY . .

ENV FLASK_APP=main
ENV FLASK_ENV=production
ENV PORT=9000

EXPOSE 9000

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:9000/health || exit 1

CMD ["gunicorn", "--bind", "0.0.0.0:9000", "--workers", "4", "--timeout", "120", "main:app"]

# Add prometheus_client to the requirements
RUN pip install prometheus_client
