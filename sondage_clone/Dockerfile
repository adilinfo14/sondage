FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY sondage_clone /app/sondage_clone

RUN adduser --disabled-password --gecos "" appuser
RUN mkdir -p /app/data && chown -R appuser:appuser /app

USER appuser
WORKDIR /app/sondage_clone

ENV PORT=5050
ENV SONDAGE_DB_PATH=/app/data/sondage.db

EXPOSE 5050

CMD ["gunicorn", "--bind", "0.0.0.0:5050", "--workers", "2", "--threads", "4", "--timeout", "60", "app:app"]
