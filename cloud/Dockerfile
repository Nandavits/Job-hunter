FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt /app/requirements-jobhunter.txt
COPY cloud/requirements.txt /app/requirements-cloud.txt
RUN pip install --no-cache-dir -r requirements-jobhunter.txt -r requirements-cloud.txt
COPY jobhunter /app/jobhunter
COPY config/*.example.yaml /app/config/
COPY cloud /app/cloud
ENV PYTHONPATH=/app/cloud:/app PYTHONUNBUFFERED=1
RUN useradd --create-home app && mkdir -p /data && chown app /data
USER app
