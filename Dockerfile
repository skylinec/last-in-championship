FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV FLASK_APP=app.main
ENV FLASK_ENV=production

CMD ["gunicorn", "--bind", "0.0.0.0:8000", "main:app"]
