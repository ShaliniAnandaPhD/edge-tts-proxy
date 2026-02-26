FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY edge_tts_server.py .
ENV PORT=8080
EXPOSE 8080
CMD ["python3", "edge_tts_server.py", "--port", "8080"]
