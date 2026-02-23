FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1     PYTHONUNBUFFERED=1

# (опционально) tzdata полезна для корректных дат/логов
RUN apt-get update && apt-get install -y --no-install-recommends tzdata     && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "main.py"]
