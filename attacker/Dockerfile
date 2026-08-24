FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY locustfile.py run_attack.py generate_report.py ./
COPY config ./config
RUN mkdir -p results

CMD ["python", "run_attack.py", "--config", "config/attack.json"]
