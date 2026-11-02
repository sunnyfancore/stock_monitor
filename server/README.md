# 加密中转服务（server/）

让客户端不直连腾讯：客户端 → **你的服务器（按 IP 直连 + TLS 加密 + 证书指纹固定）** → 腾讯行情/搜索。

网络上（公司网关、抓包工具、同一 Wi-Fi）只能看到「这台电脑连了某个 IP 的某个端口，数据已加密」，
看不到腾讯域名、看不到查询了哪些股票，也无法用企业根证书做中间人解密（客户端只认你这张证书的指纹）。

## 一、准备

1. 一台能运行 Docker 的 Linux 服务器（香港/海外最省事；国内服务器不绑域名、直接用 IP 也可以）
2. 在云服务器**安全组 / 防火墙放行端口**，默认 `8443`（公司网络若只放行 443，可改用 443，见下文）
3. 服务器上 SSH 用户能执行 `docker`（root，或已加入 docker 组）

## 二、用 GitHub Actions 自动构建和部署

1. 把整个项目推到 GitHub 仓库（建议**私有仓库**）
2. 仓库 → Settings → Secrets and variables → Actions：

   | 类型 | 名称 | 说明 |
   |---|---|---|
   | Secret | `SERVER_HOST` | 服务器公网 **IP**（不要用域名，否则 DNS 会暴露） |
   | Secret | `SERVER_USER` | SSH 用户名 |
   | Secret | `SERVER_SSH_KEY` | SSH 私钥全文（公钥已加入服务器 `~/.ssh/authorized_keys`） |
   | Secret（可选） | `SERVER_SSH_PORT` | SSH 端口，默认 22 |
   | Variable（可选） | `RELAY_PORT` | 对外端口，默认 8443 |

3. 推送 `server/` 下的改动，或在 Actions 页面手动运行 **relay** 工作流：
   构建镜像（amd64 + arm64）→ 推送到 `ghcr.io/<你的用户名>/stock-relay` → SSH 到服务器拉取并重启容器
4. 获取连接串（**只在服务器上看，Actions 日志里不会打印令牌**）：

   ```bash
   docker logs stock-relay 2>&1 | grep swr://
   ```

   形如 `swr://令牌@1.2.3.4:8443/64位十六进制指纹`

5. 客户端：右键任务栏条 → **加密中转 → 设置连接串…**，粘贴回车，然后勾选 **经服务器中转**

## 三、不用 Actions，手动部署

```bash
cd server
docker build -t stock-relay .
docker run -d --name stock-relay --restart unless-stopped \
  -p 8443:8443 -v stock-relay-data:/data \
  -e RELAY_PUBLIC_HOST=你的服务器IP -e RELAY_PUBLIC_PORT=8443 \
  --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges \
  stock-relay
docker logs stock-relay 2>&1 | grep swr://
```

## 四、常见操作

- **改用 443 端口**：把 Variable `RELAY_PORT` 设为 `443`（宿主机 443 不能被其他程序占用），重新运行工作流；
  客户端需要用新连接串（端口变了）
- **更换令牌**（怀疑泄露）：`docker exec stock-relay rm /data/token && docker restart stock-relay`，再取新连接串
- **更换证书**：`docker exec stock-relay rm /data/cert.pem /data/key.pem && docker restart stock-relay`，再取新连接串
- 证书、令牌保存在 Docker 卷 `stock-relay-data` 里，重新部署不会改变，客户端无需重新设置

## 五、安全说明

- 只有 `POST /api` 一个接口；令牌错误或任何其他请求都返回普通 nginx 404 页面，扫描者看不出用途
- 只转发「行情查询」和「搜索」两种请求，代码/关键词严格校验，不能被当成通用代理
- 服务端不记录访问日志，不留查询内容；畸形请求同样返回 nginx 风格 404，看不出是 Python 程序
- 连接串（含令牌）每次启动会打印到容器日志，方便随时取用；能登录服务器的人都能看到，
  不要把 `docker logs` 输出发给别人。怀疑泄露时按上面的方法更换令牌
- 客户端连接串只接受 **IP 地址**（用域名会产生 DNS 查询和 SNI，暴露服务器名）
- 同时最多处理 64 个连接，空闲 20 秒断开，防止慢连接耗尽资源
- 容器以非 root 用户、只读文件系统、去除全部 capabilities 运行
- 网络上仍能看到的：你连了这个 IP、连接的时间和流量大小（开盘时段规律性的小请求）
