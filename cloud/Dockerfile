FROM python:3.12-slim
WORKDIR /app
# Only what the server needs (no browser automation or test tools): a smaller, faster image
COPY cloud/requirements-runtime.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY jobhunter /app/jobhunter
COPY config/*.example.yaml /app/config/
COPY cloud /app/cloud
ENV PYTHONPATH=/app/cloud:/app PYTHONUNBUFFERED=1
RUN useradd --create-home app && mkdir -p /data && chown app /data
USER app
