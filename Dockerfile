# Imagen para Railway. Un solo proceso: FastAPI sirve el webhook y ademas
# corre el worker del buffer como tarea de arranque.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Las dependencias van en una capa aparte para que un cambio de codigo no
# reinstale todo en cada deploy.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY migrations/ ./migrations/
COPY scripts/ ./scripts/
COPY prompts/ ./prompts/
COPY config/ ./config/

# Usuario sin privilegios.
RUN useradd --create-home --uid 1000 agente && chown -R agente:agente /app
USER agente

EXPOSE 8000

# Railway inyecta PORT; en local cae en 8000.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
