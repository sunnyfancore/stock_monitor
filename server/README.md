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

**1Panel**（以 v1.10 / v2 为例，不同版本菜单位置可能略有差异）：

1. 准备一个带 HTTPS 的网站（已有就跳过）：
   - 网站 → 创建网站 → 选「反向代理」或「静态网站」→ 主域名填你的域名 → 确认
   - 网站 → 证书 → 申请证书（Let's Encrypt）→ 回到该网站的「HTTPS」页启用，选这张证书
2. 网站列表里点该网站的「设置」（或网站名称）→ 左侧「反向代理」→「创建反向代理」：
   - 名称：`stock-api`（随意）
   - 前端请求路径：`/stock-api`
   - 后端代理地址：`http://127.0.0.1:18080`
   - 发送域名：保持默认 `$host`
   - 缓存：**不要开启**
3. 保存，1Panel 会自动重载 OpenResty

如果访问出现 502（OpenResty 访问不到本机 18080）：
把 `docker-compose.yml` 里的 `"127.0.0.1:18080:8080"` 改成 `"172.17.0.1:18080:8080"`（只绑 Docker 网关，外网仍访问不到），
`docker compose up -d` 后，把后端代理地址改为 `http://172.17.0.1:18080`。

## 客户端

首次运行客户端时点「设置加密中转」（或右键任务栏条 → 高级配置 → 网络方式 → 设置连接串），粘贴：

```
swr://令牌@你的域名/stock-api
```

（令牌即 `data/token` 的内容；端口不是 443 时写成 `swr://令牌@你的域名:端口/stock-api`）

## 不使用反代（可选）

没有网站或域名时，可由服务端自己提供 HTTPS（自签证书，客户端按指纹校验）：

```yaml
services:
  relay:
    image: ghcr.io/sunnyfancore/stock-relay:latest
    container_name: stock-relay
    restart: unless-stopped
    environment:
      RELAY_TLS: "1"
    ports:
      - "8443:8443"               # 左边为对外端口，可自行修改
    volumes:
      - ./data:/data
```

`docker compose up -d` 后 `cat data/url` 得到连接串模板 `swr://令牌@服务器IP:端口/api?pin=指纹`，
把其中的服务器 IP 和端口改成实际值即可。

## 说明

- 客户端只用自带的公共根证书清单校验你网站的证书，公司或抓包工具装进电脑的根证书无效，无法解密
- 网络上能看到你的域名，看不到腾讯域名和查询内容
- 没带令牌的请求一律返回 404
- 升级：`docker compose pull && docker compose up -d`
