# 盯盘小窗 · 加密中转服务镜像
# 只用 Python 标准库；openssl 仅用于首次启动时生成自签证书
FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends openssl \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --system --uid 10001 --no-create-home relay \
 && mkdir -p /data /app \
 && chown relay /data

COPY relay.py /app/relay.py

USER relay
ENV RELAY_DATA=/data \
    RELAY_PORT=8443 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# 证书、令牌保存在卷里：重建容器不会变，客户端连接串保持有效
VOLUME ["/data"]
EXPOSE 8443

# 健康检查：按当前模式（TLS / 明文）连一次本机端口
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python /app/relay.py --health || exit 1

ENTRYPOINT ["python", "/app/relay.py"]
