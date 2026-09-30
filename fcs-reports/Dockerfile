# Una sola imagen para todos los clientes: lo unico que cambia entre
# contenedores es el .env (credenciales, CLIENT_NAME, REPORT_FREQUENCY).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# CA corporativas (opcional). En redes con inspeccion TLS -- Netskope, Zscaler,
# Palo Alto -- el certificado que ve el contenedor esta firmado por la CA de la
# empresa, que no viene en el almacen de confianza de la imagen: sin esto, pip
# falla al construir y requests/FalconPy falla en runtime contra
# api.crowdstrike.com. Se copian los .crt que haya en certs/ y se regenera el
# bundle del sistema. Si certs/ solo tiene el .gitkeep, update-ca-certificates
# no hace nada y la imagen queda igual de generica.
COPY certs/ /usr/local/share/ca-certificates/
RUN update-ca-certificates

# pip y requests no usan el bundle del sistema por default (traen el suyo, via
# certifi), asi que hay que apuntarlos explicitamente.
ENV PIP_CERT=/etc/ssl/certs/ca-certificates.crt \
    REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py ./
COPY src/ ./src/
COPY templates/ ./templates/
COPY static/ ./static/

# Usuario sin privilegios; output/ se monta como volumen para conservar el
# historico de reportes entre despliegues.
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
