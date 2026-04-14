FROM python:3.11-slim

WORKDIR /app

COPY etalon_bot/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY etalon_bot ./etalon_bot

CMD ["python", "-m", "etalon_bot.bot"]
