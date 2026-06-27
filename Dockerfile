FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY x_trending_bot.py .

# 默认每小时拉一次, 可在 docker run 时用 --interval 覆盖
ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["python", "x_trending_bot.py"]
CMD ["--interval", "3600"]
