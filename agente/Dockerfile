# Agente conversacional: embeddings locales (ONNX) + clasificador + RAG + DeepSeek (OpenRouter).
# El modelo de embeddings y el índice entrenado quedan horneados en la imagen:
# arrancar no descarga nada ni re-entrena.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    EMBED_CACHE_DIR=/models AGENTE_INDEX_DIR=/app/index AGENTE_DATA_DIR=/app/data
ARG EMBED_MODEL=Xenova/multilingual-e5-small
ENV EMBED_MODEL=${EMBED_MODEL}

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY data ./data
COPY app/__init__.py app/datos.py app/modelo.py app/entrenar.py ./app/
# Descarga el modelo, entrena los clasificadores, imprime las métricas y guarda el índice.
# Sólo depende de los datos y de estos tres módulos: tocar main.py no obliga a re-entrenar.
RUN python -m app.entrenar && chmod -R a+rX /models /app/index
# Fotos del catálogo de 100 modelos (JPG 720 px, servidas en /media/catalogo/) y copia de las de la tienda.
COPY imagenes ./imagenes
# Búsqueda por foto: indexa las fotos con el modelo de imagen y calibra los umbrales.
ARG IMAGE_MODEL=Qdrant/Unicom-ViT-B-32
ENV IMAGE_MODEL=${IMAGE_MODEL}
# 0 = indexar solo las fotos de la tienda (imagenes/tienda), sin el catálogo de 100 modelos.
ARG CATALOGO100=1
ENV CATALOGO100=${CATALOGO100}
COPY app/imagen.py ./app/
RUN python -m app.imagen && chmod -R a+rX /models /app/index
COPY seed ./seed
COPY app ./app
# La máquina de etapas (prospección → seguimiento → cierre) se prueba en cada build: si un cambio la
# rompe, la imagen no se construye.
RUN python -m app.prueba_etapas
# El modelo ya está dentro: que nada intente bajarlo de Hugging Face al arrancar.
ENV HF_HUB_OFFLINE=1

RUN useradd -r -u 10001 agente && mkdir -p /data && chown agente /data
USER agente
EXPOSE 8000
HEALTHCHECK --interval=60s --timeout=5s --start-period=60s --retries=3 \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=4)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
