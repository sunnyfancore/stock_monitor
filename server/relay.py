#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""盯盘小窗 · 加密中转服务（纯 Python 标准库，无第三方依赖）

作用：客户端只连本服务（按 IP 直连 + 证书指纹固定），由本服务去请求腾讯行情/搜索接口。
网络上只能看到“某台电脑连了某个 IP 的端口，传输内容已加密”，看不到腾讯域名，也看不到查询内容。

首次启动自动在数据目录生成：
  cert.pem / key.pem   自签证书（EC P-256，10 年）
  token                访问令牌（随机 256 位）
并在日志里打印一行连接串  swr://令牌@IP:端口/证书SHA256指纹  —— 粘贴到客户端即可。

环境变量：
  RELAY_DATA         数据目录（默认 /data）
  RELAY_PORT         监听端口（默认 8443）
  RELAY_PUBLIC_HOST  客户端连接用的服务器 IP（仅用于打印连接串；强烈建议用 IP 而不是域名）
  RELAY_PUBLIC_PORT  宿主机映射端口（仅用于打印连接串，默认同 RELAY_PORT）
  RELAY_TOKEN        指定令牌（不设则自动生成并保存）

接口：只有 POST /api，必须带 Authorization: Bearer <令牌>；其余一切请求（含令牌错误）
都回一个普通的 nginx 404 页面，扫描到这个端口也看不出它是做什么的。
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
PORT = int(os.environ.get("RELAY_PORT", "8443"))
PUBLIC_HOST = os.environ.get("RELAY_PUBLIC_HOST", "").strip()
PUBLIC_PORT = os.environ.get("RELAY_PUBLIC_PORT", "").strip() or str(PORT)

CERT = os.path.join(DATA_DIR, "cert.pem")
KEY = os.path.join(DATA_DIR, "key.pem")
TOKEN_FILE = os.path.join(DATA_DIR, "token")

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
        if self.path != "/api" or not self._authed():
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


def main():
    ensure_cert()
    token = ensure_token()
    fp = cert_fingerprint()

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(CERT, KEY)

    httpd = Server((LISTEN, PORT), Handler)
    httpd.token = token
    # 握手放到各连接自己的线程里做（do_handshake_on_connect=False），慢连接不会卡住 accept
    httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True, do_handshake_on_connect=False)

    host = PUBLIC_HOST or "<服务器IP>"
    log(f"加密中转服务已启动，容器内端口 {PORT}")
    log("证书 SHA256 指纹：" + fp)
    log("客户端连接串（在盯盘小窗右键菜单「加密中转 → 设置连接串」里粘贴）：")
    print(f"swr://{token}@{host}:{PUBLIC_PORT}/{fp}", flush=True)
    if not PUBLIC_HOST:
        log("提示：未设置 RELAY_PUBLIC_HOST，请把连接串里的 <服务器IP> 换成服务器公网 IP")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
