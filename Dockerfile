FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bridge.py .
COPY beep.wav .

EXPOSE 8080/tcp 12345/udp

ENV TTS_ENGINE=edge
ENV HTTP_HOST=0.0.0.0
ENV UDP_HOST=0.0.0.0

CMD ["python", "bridge.py"]
