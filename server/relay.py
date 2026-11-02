#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""盯盘小窗 · 加密中转服务（纯 Python 标准库，无第三方依赖）

作用：客户端只连你的服务器，由本服务去请求腾讯行情/搜索接口；网络上看不到腾讯域名和查询内容。

两种用法：
  反代（默认，推荐）：本服务明文监听 8080，由 nginx / 1Panel 在你现有网站里加一个反代路径，
                      共用 443 和网站证书；客户端用公共 CA 清单校验证书（不信任系统里后装的根证书）
  直连（RELAY_TLS=1）：本服务自己做 TLS（自签证书），客户端按指纹固定证书

首次启动在数据目录生成 token（访问令牌），直连模式另生成 cert.pem / key.pem；
连接串模板打印到日志并写入 data/url。

环境变量：
  RELAY_TLS          0 = 反代模式（默认）；1 = 直连模式
  RELAY_PORT         监听端口（默认反代 8080 / 直连 8443）
  RELAY_DATA         数据目录（默认 /data）
  RELAY_TOKEN        指定令牌（不设则自动生成并保存）
  RELAY_PUBLIC_HOST / RELAY_PUBLIC_PORT  仅直连模式用于填充连接串模板（可不设）

接口：任意路径的 POST，必须带 Authorization: Bearer <令牌>；其余一切请求（含令牌错误）
都回一个普通的 nginx 404 页面，看不出它是做什么的。
  {"op": "q", "codes": ["sh600519", ...]}   → 腾讯行情原始返回（GBK 字节）
  {"op": "s", "kw": "茅台"}                  → 腾讯搜索原始返回（UTF-8 字节）
只转发这两种请求、严格校验参数，不能被当成通用代理使用。
"""
import hashlib
import hmac
import http.client
import http.server
import json
import os
import re
import secrets
import ssl
import subprocess
import sys
import threading
import time
import urllib.parse

DATA_DIR = os.environ.get("RELAY_DATA", "/data")
LISTEN = os.environ.get("RELAY_LISTEN", "0.0.0.0")
# 默认 RELAY_TLS=0：明文 HTTP 监听 8080，给前面的 nginx / 1Panel 反代用（推荐，可与网站共用 443）
# RELAY_TLS=1：自己做 TLS（自签证书 + 客户端固定指纹），不经反代直接对外时用
TLS = os.environ.get("RELAY_TLS", "0").strip() == "1"
PORT = int(os.environ.get("RELAY_PORT", "8443" if TLS else "8080"))
PUBLIC_HOST = os.environ.get("RELAY_PUBLIC_HOST", "").strip()
PUBLIC_PORT = os.environ.get("RELAY_PUBLIC_PORT", "").strip() or str(PORT)

CERT = os.path.join(DATA_DIR, "cert.pem")
KEY = os.path.join(DATA_DIR, "key.pem")
TOKEN_FILE = os.path.join(DATA_DIR, "token")
URL_FILE = os.path.join(DATA_DIR, "url")

QUOTE_HOST = "qt.gtimg.cn"
SEARCH_HOST = "smartbox.gtimg.cn"
UPSTREAM_TIMEOUT = 5
UPSTREAM_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Referer": "https://gu.qq.com/",
    "Connection": "keep-alive",
}

CODE_RE = re.compile(r"^[A-Za-z0-9_.]{2,20}$")
MAX_CODES = 300
MAX_KW = 40
MAX_BODY = 16 * 1024

NOT_FOUND = (b"<html>\r\n<head><title>404 Not Found</title></head>\r\n<body>\r\n"
             b"<center><h1>404 Not Found</h1></center>\r\n<hr><center>nginx</center>\r\n"
             b"</body>\r\n</html>\r\n")


def log(msg):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), msg, flush=True)


# ---------------------------------------------------------------- 证书 / 令牌
def ensure_cert():
    if os.path.exists(CERT) and os.path.exists(KEY):
        return
    os.makedirs(DATA_DIR, exist_ok=True)
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1",
         "-nodes", "-days", "3650", "-subj", "/CN=localhost", "-keyout", KEY, "-out", CERT],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    os.chmod(KEY, 0o600)
    log("已生成新的自签证书（客户端需要使用新的连接串）")


TOKEN_RE = re.compile(r"^[A-Za-z0-9_\-]{16,128}$")   # 与客户端连接串规则一致


def ensure_token():
    env = os.environ.get("RELAY_TOKEN", "").strip()
    if env:
        if TOKEN_RE.match(env):
            return env
        log("RELAY_TOKEN 格式不符（需 16~128 位字母数字 _ -），改用自动生成的令牌")
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, encoding="utf-8") as f:
            t = f.read().strip()
        if TOKEN_RE.match(t):
            return t
    t = secrets.token_urlsafe(32)
    fd = os.open(TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)   # 创建时即仅本用户可读
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(t)
    log("已生成新的访问令牌")
    return t


def cert_fingerprint():
    with open(CERT, encoding="ascii") as f:
        der = ssl.PEM_cert_to_DER_cert(f.read())
    return hashlib.sha256(der).hexdigest()


# ---------------------------------------------------------------- 上游（腾讯）请求
_local = threading.local()


def upstream_get(host, path):
    """每个工作线程复用一条到上游的 https 长连接；失败自动重连重试一次"""
    conns = getattr(_local, "conns", None)
    if conns is None:
        conns = _local.conns = {}
    for attempt in (0, 1):
        c = conns.get(host)
        try:
            if c is None:
                c = conns[host] = http.client.HTTPSConnection(
                    host, 443, timeout=UPSTREAM_TIMEOUT, context=ssl.create_default_context())
            c.request("GET", path, headers=UPSTREAM_HEADERS)
            r = c.getresponse()
            data = r.read()
            if r.status != 200:
                raise OSError(f"上游 HTTP {r.status}")
            return data
        except Exception:
            try:
                if c is not None:
                    c.close()
            except Exception:
                pass
            conns.pop(host, None)
            if attempt:
                raise
    return b""


# ---------------------------------------------------------------- HTTP 处理
class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "nginx"
    sys_version = ""
    protocol_version = "HTTP/1.1"          # 支持 keep-alive，客户端一条连接反复用
    timeout = 20                            # 空闲 / 握手超时（秒）；客户端断开会自动重连

    def log_message(self, fmt, *args):      # 不记录访问日志：不在服务器上留下查询内容
        pass

    def version_string(self):               # 响应头 Server: nginx（与普通网站一致）
        return "nginx"

    def _send(self, code, body, ctype="text/html"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _not_found(self):
        self.close_connection = True
        self._send(404, NOT_FOUND)

    def send_error(self, code, message=None, explain=None):
        # 畸形请求行 / 不支持的方法 / 超长 URI 等：默认会返回 Python 风格错误页（暴露身份），
        # 一律改成与 nginx 一致的 404
        self.close_connection = True
        try:
            self._send(404, NOT_FOUND)
        except Exception:
            pass

    def do_GET(self):
        self._not_found()

    do_HEAD = do_GET
    do_PUT = do_DELETE = do_OPTIONS = do_PATCH = do_GET

    def _authed(self):
        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return False
        return hmac.compare_digest(auth[7:].strip().encode(), self.server.token.encode())

    def do_POST(self):
        # 路径不限：反代路径由你在 nginx 里自定（如 /stock-api），靠令牌鉴权
        if not self._authed():
            return self._not_found()
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = -1
        if n <= 0 or n > MAX_BODY:
            return self._not_found()
        try:
            req = json.loads(self.rfile.read(n).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return self._send(400, b"bad request", "text/plain")
        if not isinstance(req, dict):
            return self._send(400, b"bad request", "text/plain")
        op = req.get("op")
        try:
            if op == "q":
                codes = req.get("codes")
                if not (isinstance(codes, list) and 0 < len(codes) <= MAX_CODES
                        and all(isinstance(c, str) and CODE_RE.match(c) for c in codes)):
                    return self._send(400, b"bad codes", "text/plain")
                data = upstream_get(QUOTE_HOST, "/q=" + ",".join(codes))
            elif op == "s":
                kw = req.get("kw")
                if not (isinstance(kw, str) and 0 < len(kw.strip()) <= MAX_KW):
                    return self._send(400, b"bad keyword", "text/plain")
                data = upstream_get(SEARCH_HOST,
                                    "/s3/?v=2&t=all&q=" + urllib.parse.quote(kw.strip()))
            else:
                return self._send(400, b"bad op", "text/plain")
        except Exception as e:
            log(f"上游请求失败：{e!r}")
            return self._send(502, b"upstream error", "text/plain")
        self._send(200, data, "application/octet-stream")


MAX_CONN = 64                               # 同时处理的连接上限，防止慢连接耗尽线程


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._slots = threading.BoundedSemaphore(MAX_CONN)

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):   # 满了直接断开，不排队
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()

    def handle_error(self, request, client_address):
        # 扫描器、明文 HTTP 访问、握手失败等都会走到这里：一行带过，不刷屏
        err = sys.exc_info()[1]
        if not isinstance(err, (ssl.SSLError, ConnectionError, TimeoutError, OSError)):
            log(f"处理请求异常 {client_address[0]}：{err!r}")


def health():
    """容器健康检查：按当前模式连一次本机端口（TLS 模式还要完成握手）"""
    import socket
    try:
        s = socket.create_connection(("127.0.0.1", PORT), 3)
        if TLS:
            s = ssl._create_unverified_context().wrap_socket(s)
        s.close()
        return 0
    except OSError:
        return 1


def main():
    token = ensure_token()
    fp = ""
    if TLS:                  # 只有自己做 TLS 时才需要自签证书
        ensure_cert()
        fp = cert_fingerprint()

    httpd = Server((LISTEN, PORT), Handler)
    httpd.token = token
    if TLS:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(CERT, KEY)
        # 握手放到各连接自己的线程里做（do_handshake_on_connect=False），慢连接不会卡住 accept
        httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True,
                                       do_handshake_on_connect=False)

    mode = "TLS 直连（自签证书，客户端固定指纹）" if TLS else "明文 HTTP（由 nginx / 1Panel 反代并负责 HTTPS）"
    log(f"加密中转服务已启动，容器内端口 {PORT}，模式：{mode}")
    if TLS:
        log("证书 SHA256 指纹：" + fp)
    if TLS:
        url = f"swr://{token}@{PUBLIC_HOST or '服务器IP'}:{PUBLIC_PORT}/api?pin={fp}"
        hint = "直连模式：把“服务器IP:端口”换成实际值"
    else:
        url = f"swr://{token}@你的域名/反代路径"
        hint = "反代模式：把“你的域名/反代路径”换成实际值，例如 swr://令牌@example.com/stock-api"
    log("客户端连接串（盯盘小窗首次运行点「设置加密中转」，或右键菜单「网络方式 → 设置连接串」里粘贴）：")
    print(url, flush=True)
    log(hint)
    try:                     # 同时写入 data/url（仅本用户可读），随时 cat data/url 查看
        fd = os.open(URL_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(url + "\n# " + hint + "\n")
    except OSError as e:
        log(f"写入 {URL_FILE} 失败：{e!r}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    if "--health" in sys.argv:
        sys.exit(health())
    main()
