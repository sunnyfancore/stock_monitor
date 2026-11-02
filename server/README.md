# 加密中转服务

## 镜像

GitHub Actions 自动构建：`ghcr.io/sunnyfancore/stock-relay:latest`

> 仓库私有时镜像默认也私有：在 GitHub → Packages → stock-relay → 设置里改成 Public（镜像不含任何密钥），
> 或在服务器上 `docker login ghcr.io`。

## 部署

`/opt/stock-relay/docker-compose.yml`：

```yaml
services:
  relay:
    image: ghcr.io/sunnyfancore/stock-relay:latest
    container_name: stock-relay
    restart: unless-stopped
    environment:
      RELAY_TLS: "0"                  # TLS 由 nginx 负责
      RELAY_PORT: "8080"
      RELAY_PUBLIC_HOST: "1.2.3.4"    # ← 服务器公网 IP
      RELAY_PUBLIC_PORT: "8443"       # ← nginx 对外端口
    ports:
      - "127.0.0.1:18080:8080"
    volumes:
      - ./data:/data
    read_only: true
    tmpfs: [/tmp]
    cap_drop: [ALL]
```

```bash
cd /opt/stock-relay && mkdir -p data && chown 10001 data
docker compose up -d
cat data/url        # 客户端连接串
```

## nginx 反代

```nginx
server {
    listen 8443 ssl default_server;
    server_name _;
    ssl_certificate     /opt/stock-relay/data/cert.pem;
    ssl_certificate_key /opt/stock-relay/data/key.pem;
    server_tokens off;
    access_log off;

    location = /api {
        if ($http_authorization = "") { return 404; }
        proxy_pass http://127.0.0.1:18080;
    }
    location / { return 404; }
}
```

## 1Panel 反代

1. 网站 → 证书 → 上传证书（粘贴）：证书填 `data/cert.pem` 内容，私钥填 `data/key.pem` 内容
2. 网站 → 创建网站 → 反向代理：域名填 `服务器IP:8443`，代理地址 `http://127.0.0.1:18080`
3. 该网站 → HTTPS → 启用，选第 1 步的证书
4. 该网站 → 配置文件，把 `location /` 那段换成：

```nginx
    location = /api {
        if ($http_authorization = "") { return 404; }
        proxy_pass http://127.0.0.1:18080;
    }
    location / { return 404; }
```

## 注意

- 证书必须用 `data/` 里生成的那张，不能换成网站证书（客户端校验指纹）
- 用独立端口（如 8443）；若用 443，这个站点必须是 443 的默认站点
- 防火墙 / 安全组放行 8443
- 升级：`docker compose pull && docker compose up -d`
