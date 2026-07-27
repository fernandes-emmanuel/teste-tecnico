# Dockerfile para o Pipeline Híbrido de Extração de Licitações (Imagem Enxuta e Rápida)
FROM python:3.10-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=America/Sao_Paulo

# Instala dependências leves de sistema para PDF e OCR Tesseract em Português
RUN apt-get update && apt-get install -y --no-install-recommends \
    poppler-utils \
    tesseract-ocr \
    tesseract-ocr-por \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Instala dependências Python enxutas
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia código da aplicação
COPY . .

# Comando padrão de execução
CMD ["python", "main.py"]
