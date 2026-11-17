FROM python:3.12-slim

# LibreOffice turns the generated .docx files into PDFs; Liberation fonts match Times New Roman metrics.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libreoffice-writer-nogui fonts-liberation tzdata \
 && rm -rf /var/lib/apt/lists/*

ENV TZ=Europe/Berlin \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY jobhunter ./jobhunter
COPY config/*.example.yaml ./config/

# The form filler (`fill`) needs a visible browser, so it runs on your own computer, not in Docker.
CMD ["python", "-m", "jobhunter", "schedule"]
