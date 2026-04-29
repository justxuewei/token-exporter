FROM python:3.12-slim

WORKDIR /opt/token-exporter
ENV STATE_FILE=/etc/token-exporter/state.json

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 14531 14532

CMD ["python", "app.py"]
