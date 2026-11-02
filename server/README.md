# 加密中转服务 —— Docker Compose 一键部署

客户端 → **你的服务器（按 IP 直连 + TLS + 证书指纹固定）** → 腾讯行情 / 搜索。

网络上（公司网关、抓包工具、同一 Wi-Fi）只能看到「这台电脑连了某个 IP 的某个端口，数据已加密」，
看不到腾讯域名、看不到查询了哪些股票，也无法用企业根证书做中间人解密（客户端只认你这张证书的指纹）。

## 一、一键部署

服务器需要：Linux + Docker（含 compose 插件）。在服务器上：

```bash
git clone https://github.com/sunnyfancore/stock_monitor.git
cd stock_monitor/server
sudo bash install.sh
```

脚本会让你选择部署模式、自动探测公网 IP、选端口，然后构建并启动，最后**直接打印客户端连接串**。
之后在云服务器**安全组 / 防火墙放行对应端口**即可。

### 三种模式

| 模式 | 命令 | 适用 | 默认端口 |
|---|---|---|---|
| **nginx**（推荐） | `bash install.sh nginx` | 服务器上没有占用该端口的 nginx。一起启动 nginx 容器做 TLS + 反代，对外就是普通 https 站点 | 443 |
| **host-nginx** | `bash install.sh host-nginx` | 服务器上**已经装了 nginx**（含宝塔等面板）。只启动中转服务（监听 127.0.0.1），并生成一段 nginx 配置给你现有的 nginx 用 | 8443 |
| **standalone** | `bash install.sh standalone` | 不想用 nginx。中转服务自己做 TLS 直接对外 | 8443 |

不论哪种模式，TLS 用的都是中转服务自动生成的那张自签证书（nginx 与它共用），所以连接串里的指纹始终和客户端实际看到的证书一致。

### 常用命令

```bash
bash install.sh            # 按上次的设置更新 / 重建（改了 .env 后也用它）
bash install.sh url        # 查看客户端连接串（含令牌，勿外传）
bash install.sh status     # 容器状态
bash install.sh logs       # 最近日志（连接串已打码）
bash install.sh down       # 停止（证书和令牌保留，重新部署后连接串不变）
```

切换模式直接运行 `bash install.sh 新模式`，脚本会先停掉旧模式的容器。

## 二、host-nginx 模式说明（已有 nginx）

运行后会生成 `nginx/host-nginx.conf`。以 root 交互运行时会询问是否自动放到 `/etc/nginx/conf.d/` 并重载；
否则手动放进 nginx 配置目录（宝塔：`/www/server/panel/vhost/nginx/`），再 `nginx -t && systemctl reload nginx`。

**关于端口（很重要）**：客户端按 IP 连接、不带 SNI，nginx 会把连接交给该端口的 `default_server`。

- **推荐独立端口（默认 8443）**：与现有网站互不影响
- 要用 **443**：这个 server 必须是 443 的 `default_server`（现有站点里不能再有 default_server），
  否则客户端拿到的是你网站的证书，指纹对不上而拒绝连接——这是防中间人的安全设计，不是故障
- 证书必须用生成配置里写好的 `data/cert.pem` / `data/key.pem`，**不能换成网站证书**

## 三、GitHub Actions 自动部署（可选）

推送 `server/` 下的改动或手动运行 **relay** 工作流：构建镜像（amd64 + arm64）推到 GHCR →
上传部署文件到服务器 → 以无人值守方式运行同一个 `install.sh`（Actions 日志里**不会**出现令牌）。

仓库 → Settings → Secrets and variables → Actions：

| 类型 | 名称 | 说明 |
|---|---|---|
| Secret | `SERVER_HOST` | 服务器公网 **IP** |
| Secret | `SERVER_USER` | SSH 用户名（建议 root，或能执行 docker 且有免密 sudo） |
| Secret | `SERVER_SSH_KEY` | SSH 私钥全文 |
| Secret（可选） | `SERVER_SSH_PORT` | SSH 端口，默认 22 |
| Secret（可选） | `SERVER_SSH_FINGERPRINT` | 服务器 SSH 主机密钥指纹 `SHA256:…`，防止部署到冒充的服务器 |
| Variable（可选） | `RELAY_MODE` | `nginx` / `host-nginx` / `standalone`；留空 = 沿用服务器上次的模式（首次为 nginx） |
| Variable（可选） | `RELAY_PORT` | 对外端口，默认随模式 |
| Variable（可选） | `DEPLOY_DIR` | 服务器上的部署目录（相对主目录），默认 `stock-relay` |

部署后在服务器上 `cd ~/stock-relay && bash install.sh url` 获取连接串。
注：Actions 部署会把镜像地址写进 `.env`（`RELAY_IMAGE`）；之后在服务器上手动重跑 `install.sh` 会去拉这个镜像，
私有仓库需先 `docker login ghcr.io`，或把 `.env` 里的 `RELAY_IMAGE=` 清空改为本地构建。

## 四、客户端设置

右键任务栏条 → **加密中转 → 设置连接串…** → 粘贴 `swr://…` → 回车（自动开启中转）。
可在本机运行 `selftest.bat`，「加密中转服务器」一项显示「证书指纹校验通过」即成功。

## 五、维护

- **更换令牌**（怀疑泄露）：`sudo rm data/token && sudo bash install.sh`，再 `bash install.sh url` 取新连接串
- **更换证书**：`sudo rm data/cert.pem data/key.pem && sudo bash install.sh`（nginx 模式会一并使用新证书），
  客户端换新连接串；**host-nginx 模式还要 `nginx -s reload`** 让宿主机 nginx 加载新证书
- 每次运行 `install.sh` 都会重建容器（nginx 也一起重建，确保它连到新的中转容器）
- 使用 **rootless Docker** 或开启了 **SELinux** 的系统：`./data` 的属主修改 / 挂载可能需要额外处理
  （SELinux 下给 compose 文件里的 `./data:/data` 加 `:Z`）
- 证书、令牌在 `./data`，`.env` 是部署设置；这些都不在 Git 里，迁移服务器时连同目录一起拷走，连接串不变

## 六、安全说明

- nginx 模式下，没带令牌的请求由 nginx 直接返回原生 404；中转服务本身对错误令牌、畸形请求也一律回 nginx 风格 404
- 只转发「行情查询」和「搜索」两种请求，代码 / 关键词严格校验，不能被当成通用代理
- 不记录访问日志，不留查询内容；容器非 root、只读文件系统、去除全部 capabilities
- 同时最多 64 个连接，空闲 20 秒断开
- 连接串（含令牌）每次启动会写进容器日志，能登录服务器的人都能看到；`bash install.sh logs` 输出已打码
- 客户端只接受 **IP**（用域名会产生 DNS 查询和 SNI，暴露服务器名）
- 网络上仍能看到：你连了这个 IP、连接时间与流量大小（开盘时段的规律性小请求）
