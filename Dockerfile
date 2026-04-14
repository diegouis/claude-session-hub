FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Create data directory
RUN mkdir -p /app/data

# The session files need to be mounted from the host
# docker run -v ~/.claude:/root/.claude:ro -p 7777:7777 claude-session-hub
EXPOSE 7777

ENV LOG_LEVEL=INFO
ENV CLAUDE_DIR=/root/.claude
ENV DOCKER=1

CMD ["python3", "run.py"]
