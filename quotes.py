# -*- coding: utf-8 -*-
"""腾讯行情接口（纯标准库）：A股 sh/sz/bj、港股 r_hk、美股 us

- 只用标准库 http.client 长连接复用（全部 https），不依赖 requests
- 可选经自建服务器加密中转（证书指纹固定，见 Relay / server/relay.py）
- 请求失败自动重连重试一次；解析容错，任何单只票异常不影响其它
"""
import datetime
import hashlib
import http.client
import json
import os
import re
import ssl
import sys
import urllib.parse

HOST = "qt.gtimg.cn"
TIMEOUT = 4
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Referer": "https://gu.qq.com/",
    "Connection": "keep-alive",
}


def normalize(code):
    """把用户输入规范化为腾讯接口代码：600519→sh600519, hk00700→r_hk00700, AAPL→usAAPL"""
    code = (code or "").strip()
    if not code:
        return ""
    c = code.lower()
    if c.startswith("us") and len(c) > 2:
        return "us" + code[2:].upper()
    if c.startswith(("sh", "sz", "bj")) and c[2:].isdigit():
        return c
    if c.startswith(("jj", "of")) and c[2:].isdigit():     # 场外基金：jj005827 / of005827
        return "jj" + c[2:]
    if c.startswith(("r_hk", "hk")):
        rest = code.strip()[4:] if c.startswith("r_hk") else code.strip()[2:]
        if rest.isdigit():
            return "r_hk" + rest.zfill(5)
        if rest.isascii() and rest.isalnum():         # 港股指数区分大小写：r_hkHSI 有效，r_hkhsi 无数据
            return "r_hk" + rest.upper()
        return ""
    if c.isdigit():
        if len(c) <= 5:                                # 5 位及以下纯数字按港股处理
            return "r_hk" + c.zfill(5)
        if c.startswith(("4", "8", "92")):      # 北交所（含 2024 起的 920xxx 新代码）
            return "bj" + c
        if c.startswith(("6", "9", "5")):
            return "sh" + c
        return "sz" + c
    if c.isascii() and c.replace(".", "").isalpha():     # 只认英文字母；中文名交给搜索
        return "us" + code.upper()
    return ""


def looks_like_code(text):
    """输入看起来就是代码（数字 / 带市场前缀），不必搜索也能直接添加"""
    t = (text or "").strip().lower()
    if not t or not t.isascii():
        return False
    return any(ch.isdigit() for ch in t) or (t.startswith("us") and len(t) > 2)


def pretty_code(code):
    """展示用代码：600519 / 00700 / AAPL"""
    c = normalize(code)
    if c.startswith("r_hk"):
        return c[4:]
    if c.startswith("us"):
        return c[2:]
    if c[:2] in ("sh", "sz", "bj", "jj"):
        return c[2:]
    return c


def market_key(code):
    """市场分组：a / hk / us / jj（节假日识别按组进行）"""
    c = normalize(code)
    if c.startswith("r_hk"):
        return "hk"
    if c.startswith("us"):
        return "us"
    if c.startswith("jj"):
        return "jj"
    return "a"


def _us_dst(d):
    """美国夏令时：3 月第二个周日 ~ 11 月第一个周日"""
    m = datetime.date(d.year, 3, 8)
    start = m + datetime.timedelta(days=(6 - m.weekday()) % 7)
    n = datetime.date(d.year, 11, 1)
    end = n + datetime.timedelta(days=(6 - n.weekday()) % 7)
    return start <= d < end


def us_eastern(now=None):
    """北京时间 → 美东时间（夏令时 -12h，冬令时 -13h）"""
    now = now or datetime.datetime.now()
    et = now - datetime.timedelta(hours=12)
    if not _us_dst(et.date()):
        et = now - datetime.timedelta(hours=13)
    return et


def market_open(code, now=None):
    """该代码所属市场此刻是否在交易（含午休判断，北京时间输入）"""
    now = now or datetime.datetime.now()
    wd = now.weekday()
    hm = now.hour * 100 + now.minute
    c = normalize(code)
    if c.startswith("us"):                  # 美股：美东 09:30-16:00，按夏令时精确换算
        et = us_eastern(now)
        if et.weekday() >= 5:
            return False
        m = et.hour * 100 + et.minute
        return 925 <= m <= 1605
    if wd >= 5:
        return False
    if c.startswith("jj"):                  # 场外基金：净值每天只更新一次，不需要高频刷新
        return False
    if c.startswith("r_hk"):                # 港股 09:30-12:00 / 13:00-16:10
        return 925 <= hm <= 1205 or 1255 <= hm <= 1615
    return 912 <= hm <= 1133 or 1255 <= hm <= 1503   # A股


def _to_quote(key, f):
    try:
        price = float(f[3])
        prev = float(f[4])
    except (ValueError, IndexError):
        return None
    if price <= 0:                           # 停牌/集合竞价前价格为 0，用昨收兜底
        price = prev
    pct = (price - prev) / prev * 100 if prev else 0.0
    raw = f[3].strip()
    dec = len(raw.split(".")[1]) if "." in raw else 2
    dec = min(max(dec, 2), 3)
    # 行情时间（字段 30）：A股 20261030152031 / 港股 2026/10/30 15:20:41 / 美股 2026-10-29 16:00:01
    ts = "".join(ch for ch in (f[30] if len(f) > 30 else "") if ch.isdigit())
    return {
        "key": key,
        "name": f[1].strip() or key,
        "price": price,
        "price_s": f"{price:.{dec}f}",
        "prev_close": prev,
        "pct": round(pct, 2),
        "date": ts[:8] if len(ts) >= 8 else "",       # YYYYMMDD，用于识别节假日休市
    }


def _to_fund_quote(key, f):
    """场外基金：v_jj005827="代码~名称~估算净值~估算涨幅~~单位净值~累计净值~日涨幅%~净值日期~"
    盘中若有估值（字段 2/3 非 0）优先用估值，否则用最新公布的净值"""
    try:
        est, est_pct = float(f[2] or 0), float(f[3] or 0)
        nav, pct = float(f[5]), float(f[7] or 0)
    except (ValueError, IndexError):
        return None
    estimated = est > 0
    price = est if estimated else nav
    pct = est_pct if estimated else pct
    if price <= 0:
        return None
    return {
        "key": key,
        "name": f[1].strip() or key,
        "price": price,
        "price_s": f"{price:.4f}",
        "prev_close": price / (1 + pct / 100) if pct > -100 else price,
        "pct": round(pct, 2),
        "fund": True,
        "estimated": estimated,
        "date": f[8].strip() if len(f) > 8 else "",
    }


# ================================================================ 加密中转（自建服务器）
# 连接串：swr://令牌@主机[:端口][/路径][?pin=证书SHA256指纹]
#   反代（推荐）：swr://令牌@example.com/stock-api        端口默认 443，证书用公共 CA 清单校验
#   直连：        swr://令牌@1.2.3.4:8443/api?pin=<64位十六进制>   自签证书，按指纹固定
#   兼容旧格式：  swr://令牌@1.2.3.4:8443/<64位十六进制>
_RELAY_RE = re.compile(
    r"^swr://([A-Za-z0-9_\-]{16,128})@(\[[0-9A-Fa-f:]+\]|[A-Za-z0-9.\-]+)(?::(\d{1,5}))?"
    r"(/[A-Za-z0-9._~\-/]*)?(?:\?pin=([0-9A-Fa-f]{64}))?$")
_RELAY_CODE_RE = re.compile(r"^[A-Za-z0-9_.]{2,20}$")


class PinError(Exception):
    """服务器证书校验失败（指纹不符 / 不是公共 CA 签发）：疑似被中间人拦截，拒绝连接"""


class RelayAuthError(Exception):
    """令牌错误（服务器回 404）"""


def parse_relay(text):
    """解析连接串，返回 {"token","host","port","path","fp"}（fp 为空 = 公共 CA 校验）；格式不对返回 None"""
    m = _RELAY_RE.match((text or "").strip())
    if not m:
        return None
    token, host, port, path, fp = m.groups()
    host = host.strip("[]")
    port = int(port) if port else 443
    if not 0 < port < 65536:
        return None
    path = path or "/api"
    if not fp and re.fullmatch(r"/[0-9A-Fa-f]{64}/?", path):   # 旧格式：路径位置放的是指纹
        fp, path = path.strip("/"), "/api"
    return {"token": token, "host": host, "port": port, "path": path.rstrip("/") or "/",
            "fp": (fp or "").lower()}


def _ca_bundle():
    """自带的公共根证书清单（Mozilla / certifi）。打包进 exe；源码运行时用已安装的 certifi 或 pip 自带的那份。
    只信任这份清单，公司 / 抓包工具装进 Windows 系统的根证书一律无效。找不到时返回 None。"""
    import importlib
    cands = []
    base = getattr(sys, "_MEIPASS", None)
    if base:
        cands.append(os.path.join(base, "cacert.pem"))
    cands.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "cacert.pem"))
    for mod in ("certifi", "pip._vendor.certifi"):        # 字符串导入：打包时不会把 pip 带进 exe
        try:
            cands.append(importlib.import_module(mod).where())
        except Exception:
            pass
    for p in cands:
        if p and os.path.exists(p):
            return p
    return None


_RELAY_CTX = None


def _relay_ca_ctx():
    global _RELAY_CTX
    if _RELAY_CTX is None:
        bundle = _ca_bundle()
        if bundle:
            _RELAY_CTX = ssl.create_default_context(cafile=bundle)   # 只加载这份清单，不读系统证书库
        else:
            _RELAY_CTX = ssl.create_default_context()                # 兜底：系统证书库（防拦截能力较弱）
        _RELAY_CTX.minimum_version = ssl.TLSVersion.TLSv1_2
    return _RELAY_CTX


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """只认指定证书指纹的 HTTPS 连接。
    不使用系统证书库（公司/抓包工具装进系统的根证书无效），握手后立即比对指纹，
    不符则在发送任何数据（含令牌）之前断开。按 IP 连接时不发送 SNI，不暴露任何域名。"""

    def __init__(self, host, port, fp, timeout):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE           # 由下面的指纹比对代替证书链校验
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        super().__init__(host, port, timeout=timeout, context=ctx)
        self._fp = fp

    def connect(self):
        super().connect()
        der = self.sock.getpeercert(binary_form=True)
        if not der or hashlib.sha256(der).hexdigest() != self._fp:
            try:
                self.sock.close()
            finally:
                self.sock = None
            raise PinError("服务器证书指纹不符，疑似被拦截")


class Relay:
    """到自建中转服务器的长连接客户端（只在工作线程里用）"""

    def __init__(self, cfg):
        self.cfg = cfg
        self.conn = None

    def close(self):
        if self.conn is not None:
            try:
                self.conn.close()
            except Exception:
                pass
        self.conn = None

    def call(self, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Authorization": "Bearer " + self.cfg["token"],
                   "Content-Type": "application/json", "Connection": "keep-alive"}
        for attempt in (0, 1):
            try:
                if self.conn is None:
                    if self.cfg.get("fp"):        # 直连模式：按指纹固定自签证书
                        self.conn = _PinnedHTTPSConnection(self.cfg["host"], self.cfg["port"],
                                                           self.cfg["fp"], TIMEOUT + 4)
                    else:                         # 反代模式：公共 CA 清单 + 域名校验
                        self.conn = http.client.HTTPSConnection(
                            self.cfg["host"], self.cfg["port"], timeout=TIMEOUT + 4,
                            context=_relay_ca_ctx())
                self.conn.request("POST", self.cfg.get("path") or "/api", body=body,
                                  headers=headers)
                resp = self.conn.getresponse()
                data = resp.read()
                if resp.status == 404:
                    raise RelayAuthError("令牌或反代路径不对")
                if resp.status != 200:
                    raise OSError(f"中转服务器 HTTP {resp.status}")
                return data
            except ssl.SSLCertVerificationError as e:
                self.close()                      # 证书不被公共 CA 认可：多半是被拦截解密
                raise PinError(f"服务器证书校验失败：{e.verify_message}") from e
            except (PinError, RelayAuthError):
                self.close()
                raise                              # 这两类错误重试也没用
            except Exception:
                self.close()
                if attempt:
                    raise
        return b""


_DEFAULT_CTX = None


def _https_ctx():
    """直连腾讯用的标准证书校验上下文（复用）"""
    global _DEFAULT_CTX
    if _DEFAULT_CTX is None:
        _DEFAULT_CTX = ssl.create_default_context()
    return _DEFAULT_CTX


class Fetcher:
    """单连接复用的行情抓取器（只在工作线程里用）

    relay：中转配置（parse_relay 的结果）；None = 直连腾讯（https）
    allow_direct：中转失败时是否允许退回直连（会暴露腾讯域名）"""

    def __init__(self, relay=None, allow_direct=True):
        self.conn = None
        self.relay = Relay(relay) if relay else None
        self.allow_direct = allow_direct
        self.route = "direct"           # 最近一次实际走的线路：relay / direct
        self.relay_error = None         # 最近一次 fetch 中中转失败的原因（None = 没失败）

    def close(self):
        if self.conn is not None:
            try:
                self.conn.close()
            except Exception:
                pass
        self.conn = None
        if self.relay is not None:
            self.relay.close()

    def _get(self, path):
        if self.conn is None:                     # 直连也走 https：内容加密
            self.conn = http.client.HTTPSConnection(HOST, 443, timeout=TIMEOUT,
                                                    context=_https_ctx())
        self.conn.request("GET", path, headers=HEADERS)
        resp = self.conn.getresponse()
        data = resp.read()
        if resp.status != 200:
            raise OSError(f"HTTP {resp.status}")
        if resp.getheader("Connection", "").lower() == "close":
            self.close()
        return data

    BATCH = 60                               # 每次最多查 60 只，自选很多时分批，避免 URL 过长

    def fetch(self, codes):
        """返回 {规范代码: quote 或 None}；全部批次都失败时才抛异常（部分成功照常返回）"""
        self.relay_error = None
        seen, uniq = set(), []
        for c in (normalize(x) for x in codes):
            if c and c not in seen:
                seen.add(c)
                uniq.append(c)
        result, err = {c: None for c in uniq}, None
        for i in range(0, len(uniq), self.BATCH):
            try:
                result.update(self._fetch_chunk(uniq[i:i + self.BATCH]))
            except Exception as e:
                err = e
        if err is not None and not any(result.values()):
            raise err
        return result

    def _raw(self, codes):
        """取原始行情文本：优先走中转；中转失败且允许直连时才直连腾讯"""
        if self.relay is not None:
            self.route = "relay"
            try:
                safe = [c for c in codes if _RELAY_CODE_RE.match(c)]   # 与服务端校验规则一致
                data = self.relay.call({"op": "q", "codes": safe})
                return data
            except Exception as e:
                self.relay_error = e               # 即使退回直连成功，也要让界面知道中转出了问题
                if not self.allow_direct:
                    self.route = "failed"          # 没有直连：不能让界面误报“已直连”
                    raise
        if not self.allow_direct:                  # 硬性保证：不许直连时绝不连腾讯
            self.route = "failed"
            raise OSError("已禁止直连")
        path = "/q=" + ",".join(codes)
        for attempt in (0, 1):
            try:
                data = self._get(path)
                self.route = "direct"
                return data
            except Exception:
                if self.conn is not None:
                    try:
                        self.conn.close()
                    except Exception:
                        pass
                    self.conn = None
                if attempt:
                    raise
        return b""

    def _fetch_chunk(self, codes):
        result = {c: None for c in codes}
        if not codes:
            return result
        data = self._raw(codes)
        text = data.decode("gbk", errors="replace")

        parsed = {}
        for line in text.split(";"):
            line = line.strip()
            if '="' not in line:
                continue
            key, _, val = line.partition('="')
            key = key.strip()
            if key.startswith("v_"):
                key = key[2:]
            fields = val.rstrip('"').split("~")
            if key.lower().startswith("jj"):           # 场外基金字段少，单独解析
                if len(fields) >= 8:
                    parsed[key.lower()] = (key, fields)
                continue
            if len(fields) < 33 or not fields[3]:
                continue
            parsed[key.lower()] = (key, fields)

        for want in codes:
            w = want.lower()
            hit = parsed.get(w)
            if hit is None:                  # 美股键可能带 .N/.OQ 后缀，前缀兜底
                for k, v in parsed.items():
                    if k.startswith(w + "."):
                        hit = v
                        break
            if hit:
                result[want] = (_to_fund_quote(*hit) if w.startswith("jj")
                                else _to_quote(*hit))
        return result


# ================================================================ 名称 / 拼音 / 代码搜索
# 腾讯 smartbox 接口：返回 v_hint="sh~600519~贵州茅台~gzmt~GP-A^hk~00700~..."
# 每条为 市场~代码~名称~拼音缩写~类型，用 ^ 分隔；无结果时为 "N"。该接口只支持 https。
SEARCH_HOST = "smartbox.gtimg.cn"
_MKT = {"sh": "沪", "sz": "深", "bj": "北", "hk": "港", "us": "美", "jj": "场外"}
_TYP = {"GP-A": "A", "GP-B": "B", "GP": "股", "ZS": "指数", "ETF": "ETF", "LOF": "LOF",
        "JJ": "基金", "KJ": "基金", "FJ": "基金", "ZQ": "债"}


def parse_hint(text, limit=9):
    i, j = text.find('"'), text.rfind('"')
    body = text[i + 1:j] if 0 <= i < j else ""
    if not body or body == "N":
        return []
    try:
        body = json.loads('"' + body + '"')            # 解 \uXXXX
    except ValueError:                                  # 兜底：只替换 \uXXXX，不丢中文
        body = re.sub(r"\\u([0-9a-fA-F]{4})", lambda mm: chr(int(mm.group(1), 16)), body)
    out, seen = [], set()
    for item in body.split("^"):
        f = item.split("~")
        if len(f) < 5:
            continue
        mkt, code, name, _py, typ = (x.strip() for x in f[:5])
        if typ == "QZ" or not code:                   # 跳过港股窝轮 / 牛熊证
            continue
        if mkt in ("sh", "sz", "bj", "jj"):           # jj = 场外基金
            n = mkt + code
        elif mkt == "hk":
            n = "r_hk" + code
        elif mkt == "us":                               # 只去掉交易所后缀 .oq/.n/.am/.ps
            base, _, suf = code.rpartition(".")
            n = "us" + (base if base and suf.lower() in ("oq", "n", "am", "ps") else code).upper()
        else:
            continue
        n = normalize(n)                                # 与行情接口的键保持一致
        if not n or n in seen:
            continue
        seen.add(n)
        out.append({"code": n, "name": name or pretty_code(n),
                    "label": _MKT.get(mkt, mkt) + _TYP.get(typ, typ)})
        if len(out) >= limit:
            break
    return out


class Searcher:
    """https 长连接复用的搜索器（只在搜索线程里用）；relay / allow_direct 含义同 Fetcher"""

    def __init__(self, relay=None, allow_direct=True):
        self.conn = None
        self.relay = Relay(relay) if relay else None
        self.allow_direct = allow_direct

    def close(self):
        if self.conn is not None:
            try:
                self.conn.close()
            except Exception:
                pass
        self.conn = None
        if self.relay is not None:
            self.relay.close()

    def search(self, keyword, limit=9):
        keyword = (keyword or "").strip()
        if not keyword:
            return []
        if self.relay is not None:                 # 优先走自建中转
            try:
                data = self.relay.call({"op": "s", "kw": keyword[:40]})   # 服务端上限 40 字
                return parse_hint(data.decode("utf-8", errors="replace"), limit)
            except Exception:
                if not self.allow_direct:
                    raise
        if not self.allow_direct:                  # 硬性保证：不许直连时绝不连腾讯
            raise OSError("已禁止直连")
        path = "/s3/?v=2&t=all&q=" + urllib.parse.quote(keyword)
        for attempt in (0, 1):
            try:
                if self.conn is None:
                    self.conn = http.client.HTTPSConnection(SEARCH_HOST, 443, timeout=TIMEOUT,
                                                            context=_https_ctx())
                self.conn.request("GET", path, headers=HEADERS)
                resp = self.conn.getresponse()
                data = resp.read()
                if resp.status != 200:
                    raise OSError(f"HTTP {resp.status}")
                return parse_hint(data.decode("utf-8", errors="replace"), limit)
            except Exception:
                self.close()
                if attempt:
                    raise
        return []
