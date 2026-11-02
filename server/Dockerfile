# 盯盘小窗 · 加密中转服务镜像（只用 Python 标准库；openssl 仅直连模式生成自签证书时用）
FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends openssl \
 && rm -rf /var/lib/apt/lists/* \
 && mkdir -p /data /app

COPY relay.py /app/relay.py

# 不切换用户：./data 直接挂载到 compose 同级目录即可写入，无需手动改权限
ENV RELAY_DATA=/data \
    RELAY_TLS=0 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

VOLUME ["/data"]
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python /app/relay.py --health || exit 1

ENTRYPOINT ["python", "/app/relay.py"]
