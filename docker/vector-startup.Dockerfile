FROM python:3.12-slim
WORKDIR /opt/vector-startup
COPY scripts/requirements-data.txt ./requirements-data.txt
RUN pip install --no-cache-dir -r requirements-data.txt pyOpenSSL==24.2.1
COPY scripts/highlight_corpus.py scripts/vector_startup.py ./
COPY data/artifacts/document-vectors.dvc data/artifacts/highlight-corpus.dvc ./pointers/
ENV DVC_NO_ANALYTICS=1 PYTHONDONTWRITEBYTECODE=1
CMD ["python", "-I", "/opt/vector-startup/vector_startup.py"]
