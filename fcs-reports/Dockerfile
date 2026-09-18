FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py ./
COPY src/ ./src/
COPY templates/ ./templates/
COPY static/ ./static/


RUN useradd --create-home --uid 10001 fcs \
    && mkdir -p /app/output/reports /app/output/raw \
    && chown -R fcs:fcs /app
USER fcs

VOLUME ["/app/output"]
EXPOSE 8000

# Modo servidor (default). Para el lote:
#   docker run --rm --env-file .env.cliente -v fcs_out:/app/output \
#              fcs-reports python -m src.generate
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
