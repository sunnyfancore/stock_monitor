# 加密中转服务

镜像由 GitHub Actions 自动构建：`ghcr.io/sunnyfancore/stock-relay:latest`
（仓库私有时，到 GitHub → Packages → stock-relay 设置里改为 Public，或服务器上 `docker login ghcr.io`）

## 部署

`docker-compose.yml`：

```yaml
services:
  relay:
    image: ghcr.io/sunnyfancore/stock-relay:latest
    container_name: stock-relay
    restart: unless-stopped
    ports:
      - "127.0.0.1:18080:8080"    # 只给本机 nginx 反代用；左边端口可自行修改
    volumes:
      - ./data:/data              # 令牌保存在同级 data 目录
```

```bash
docker compose up -d
cat data/token      # 访问令牌
```

## 反代

在你**现有网站**（已有域名和 HTTPS 证书，共用 443）里加一个路径，路径名自己定，例如 `/stock-api`。

**nginx**：在网站的 `server { ... }` 里加：

```nginx
location = /stock-api {
    proxy_pass http://127.0.0.1:18080;
}
```

**1Panel**：网站 → 选择网站 → 反向代理 → 创建：前端请求路径 `/stock-api`，后端代理地址 `http://127.0.0.1:18080`

## 客户端

右键任务栏条 → 加密中转 → 设置连接串，粘贴：

```
swr://令牌@你的域名/stock-api
```

（令牌即 `data/token` 的内容；端口不是 443 时写成 `swr://令牌@你的域名:端口/stock-api`）

## 说明

- 客户端只用自带的公共根证书清单校验你网站的证书，公司或抓包工具装进电脑的根证书无效，无法解密
- 网络上能看到你的域名，看不到腾讯域名和查询内容
- 没带令牌的请求一律返回 404
- 升级：`docker compose pull && docker compose up -d`
