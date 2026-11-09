FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HF_HOME=/models/huggingface
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends fonts-dejavu-core && rm -rf /var/lib/apt/lists/*
COPY requirements.txt requirements-ml.txt constraints.txt ./
RUN pip install --no-cache-dir -c constraints.txt -r requirements.txt
ARG INSTALL_RERANKER=true
ARG TORCH_FLAVOR=cpu
RUN case "$TORCH_FLAVOR" in cpu|cu126) ;; *) echo "TORCH_FLAVOR must be cpu or cu126" >&2; exit 1;; esac; \
    if [ "$INSTALL_RERANKER" = "true" ]; then pip install --no-cache-dir torch==2.7.0 --index-url "https://download.pytorch.org/whl/$TORCH_FLAVOR" && pip install --no-cache-dir -c constraints.txt -r requirements-ml.txt; fi
COPY . .
RUN useradd -m -u 10001 appuser && mkdir -p /models/huggingface /data/b1 && chown -R appuser:appuser /models /app /data/b1
USER appuser
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

