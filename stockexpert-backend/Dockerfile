FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HF_HOME=/models/huggingface
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends fonts-dejavu-core && rm -rf /var/lib/apt/lists/*
COPY requirements.txt requirements-ml.txt constraints.txt ./
RUN pip install --no-cache-dir -c constraints.txt -r requirements.txt
ARG INSTALL_RERANKER=true
RUN if [ "$INSTALL_RERANKER" = "true" ]; then pip install --no-cache-dir torch==2.7.0 --index-url https://download.pytorch.org/whl/cpu && pip install --no-cache-dir -c constraints.txt -r requirements-ml.txt; fi
COPY . .
RUN useradd -m -u 10001 appuser && mkdir -p /models/huggingface && chown -R appuser:appuser /models /app
USER appuser
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

