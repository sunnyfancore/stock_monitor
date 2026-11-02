# -*- coding: utf-8 -*-
"""盯盘小窗 自检脚本（不启动界面、不改配置）

检查：源码语法 / 模块能否导入 / 代码规范化 / 开盘时间判断 / 搜索结果解析 / 实时行情接口 / 搜索接口
用法：双击 selftest.bat，或 python selftest.py
"""
import datetime
import os
import py_compile
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
results = []


def check(name, fn):
    try:
        msg = fn()
        results.append((True, name, msg or ""))
    except Exception as e:                      # noqa: BLE001 —— 自检要把所有异常都报出来
        detail = "".join(traceback.format_exception_only(type(e), e)).strip()
        results.append((False, name, detail))


def eq(got, want, what=""):
    if got != want:
        raise AssertionError(f"{what} 期望 {want!r}，实际 {got!r}")


# ---------------------------------------------------------------- 1. 语法
def t_syntax():
    for f in ("winapi.py", "quotes.py", "app.py"):
        py_compile.compile(os.path.join(HERE, f), doraise=True)
    return "3 个文件均可编译"


# ---------------------------------------------------------------- 2. 导入（会执行模块级代码，能发现名字错误）
def t_import():
    import quotes                                # noqa: F401
    if sys.platform == "win32":
        import winapi                            # noqa: F401
        import app                               # noqa: F401  只导入，不会启动界面
        return "quotes / winapi / app 导入成功"
    return "非 Windows：仅导入 quotes"


# ---------------------------------------------------------------- 3. 代码规范化
def t_normalize():
    from quotes import normalize, pretty_code
    cases = {
        "600519": "sh600519", "000001": "sz000001", "300750": "sz300750",
        "510300": "sh510300", "159915": "sz159915", "588200": "sh588200",
        "830799": "bj830799", "920118": "bj920118",
        "00700": "r_hk00700", "hk700": "r_hk00700", "hk00700": "r_hk00700",
        "hkHSI": "r_hkHSI", "r_hkhsi": "r_hkHSI",
        "AAPL": "usAAPL", "usAAPL": "usAAPL", "aapl": "usAAPL",
        "jj005827": "jj005827", "of005827": "jj005827",
        "茅台": "", "": "",
    }
    for src, want in cases.items():
        eq(normalize(src), want, f"normalize({src!r})")
    eq(pretty_code("r_hkHSI"), "HSI", "pretty_code")
    eq(pretty_code("jj005827"), "005827", "pretty_code")
    return f"{len(cases)} 个用例通过"


# ---------------------------------------------------------------- 4. 开盘时间
def t_market_hours():
    from quotes import market_open
    D = datetime.datetime
    cases = [
        ("sh600519", D(2026, 10, 30, 10, 0), True, "A股 周五上午"),
        ("sh600519", D(2026, 10, 30, 12, 0), False, "A股 午休"),
        ("sh600519", D(2026, 10, 30, 14, 0), True, "A股 下午"),
        ("sh600519", D(2026, 10, 30, 15, 30), False, "A股 收盘后"),
        ("sh600519", D(2026, 10, 31, 10, 0), False, "A股 周六"),
        ("r_hk00700", D(2026, 10, 30, 15, 50), True, "港股 下午"),
        ("r_hk00700", D(2026, 10, 30, 12, 30), False, "港股 午休"),
        ("usAAPL", D(2026, 7, 1, 22, 0), True, "美股 夏令时 美东10:00"),
        ("usAAPL", D(2026, 12, 2, 22, 0), False, "美股 冬令时 美东09:00 未开盘"),
        ("usAAPL", D(2026, 12, 2, 23, 0), True, "美股 冬令时 美东10:00"),
        ("usAAPL", D(2026, 10, 31, 10, 0), False, "美股 北京周六上午(美东周五22点)"),
        ("jj005827", D(2026, 10, 30, 10, 0), False, "场外基金 不算开盘"),
    ]
    for code, t, want, what in cases:
        eq(market_open(code, t), want, what)
    return f"{len(cases)} 个用例通过"


# ---------------------------------------------------------------- 5. 搜索结果解析（离线样本）
def t_parse_hint():
    from quotes import parse_hint
    sample = ('v_hint="sh~600519~\\u8d35\\u5dde\\u8305\\u53f0~gzmt~GP-A'
              '^hk~00700~\\u817e\\u8baf\\u63a7\\u80a1~txkg~GP'
              '^us~aapl.oq~Apple~apple~GP'
              '^hk~13005~\\u8f6e~x~QZ'
              '^hk~HSI~\\u6052\\u751f\\u6307\\u6570~hszs~ZS'
              '^jj~005827~\\u6613\\u65b9\\u8fbe~yfd~KJ"')
    got = [r["code"] for r in parse_hint(sample)]
    eq(got, ["sh600519", "r_hk00700", "usAAPL", "r_hkHSI", "jj005827"], "解析结果")
    eq(parse_hint(sample)[0]["name"], "贵州茅台", "中文名")
    eq(parse_hint('v_hint="N"'), [], "无结果")
    return "解析正确（窝轮已过滤、港股指数大小写正确）"


# ---------------------------------------------------------------- 6. 中转连接串解析（离线）
def t_parse_relay():
    from quotes import parse_relay
    fp = "ab" * 32
    tok = "abcdefghijklmnopqrstuvwxyz0123456789_-ABCDE"
    good = {
        f"swr://{tok}@example.com/stock-api": ("example.com", 443, "/stock-api", ""),
        f"swr://{tok}@example.com:8443/a/b": ("example.com", 8443, "/a/b", ""),
        f"swr://{tok}@example.com": ("example.com", 443, "/api", ""),
        f"swr://{tok}@1.2.3.4:8443/api?pin={fp}": ("1.2.3.4", 8443, "/api", fp),
        f"swr://{tok}@1.2.3.4:8443/{fp}": ("1.2.3.4", 8443, "/api", fp),       # 旧格式
    }
    for s, want in good.items():
        r = parse_relay(s)
        if not r:
            raise AssertionError(f"合法连接串解析失败：{s[:40]}…")
        eq((r["host"], r["port"], r["path"], r["fp"]), want, "解析结果")
    for bad in ("", "swr://short@example.com/api", f"http://{tok}@example.com/api",
                f"swr://{tok}@example.com/api?pin=abc", f"swr://{tok}@exa mple.com/api"):
        eq(parse_relay(bad), None, f"非法连接串 {bad[:24]}…")
    return f"{len(good)} 个合法 / 5 个非法用例判断正确"


def t_ca_bundle():
    from quotes import _ca_bundle
    p = _ca_bundle()
    if not p:
        raise AssertionError("找不到公共根证书清单 cacert.pem（pip install certifi）")
    return f"使用 {p}"


def _relay_settings():
    """读取客户端配置里的中转设置（DPAPI 解密，只在本机本用户下可用）"""
    import base64
    import json
    from quotes import parse_relay
    path = os.path.join(os.environ.get("APPDATA", ""), "StockWatcher", "stocks.json")
    try:
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        return False, False, None
    on, allow = bool(cfg.get("relay_on")), bool(cfg.get("relay_allow_direct"))
    relay = None
    if cfg.get("relay_conn") and sys.platform == "win32":
        try:
            import winapi
            dec = winapi.dpapi_decrypt(base64.b64decode(cfg["relay_conn"]))
            relay = parse_relay(dec.decode("utf-8", "replace")) if dec else None
        except Exception:
            relay = None                      # 解不开就当没配置，但 on/allow 照样生效
    return on, allow, relay


try:
    RELAY_ON, RELAY_ALLOW_DIRECT, RELAY = _relay_settings()
except Exception:                                    # 读配置都失败：保守处理，不做直连测试
    RELAY_ON, RELAY_ALLOW_DIRECT, RELAY = True, False, None
NO_DIRECT = RELAY_ON and not RELAY_ALLOW_DIRECT      # 开了中转且不许直连：自检也不直连腾讯
OFFLINE = os.environ.get("SELFTEST_OFFLINE") == "1"   # CI 打包时只跑离线检查，不访问任何接口


def t_relay():
    from quotes import Fetcher, Searcher
    if OFFLINE:
        return "离线模式，跳过"
    if not RELAY:
        return "未配置中转，跳过"
    res = Fetcher(RELAY, allow_direct=False).fetch(["sh000001", "r_hkHSI"])
    if not any(res.values()):
        raise AssertionError("经中转没有取到行情")
    s = Searcher(RELAY, allow_direct=False).search("茅台")
    how = "证书指纹校验通过" if RELAY.get("fp") else "公共 CA 证书校验通过"
    return (f"{RELAY['host']}:{RELAY['port']}{RELAY.get('path', '')} {how}；"
            f"行情 {sum(1 for v in res.values() if v)} 条，搜索 {len(s)} 条")


# ---------------------------------------------------------------- 7. 直连实时接口（需要联网）
def t_live_quotes():
    if OFFLINE:
        return "离线模式，跳过"
    if NO_DIRECT:
        return "已开启中转且不允许直连：跳过直连测试"
    from quotes import Fetcher
    codes = ["sh600519", "sz300750", "r_hk00700", "r_hkHSI", "usAAPL", "jj005827", "sh000001"]
    res = Fetcher().fetch(codes)
    missing = [c for c in codes if not res.get(c)]
    if missing:
        raise AssertionError(f"这些代码没取到行情：{missing}")
    return "  ".join(f"{res[c]['name']} {res[c]['price_s']}" for c in codes[:4]) + " …"


def t_live_search():
    if OFFLINE:
        return "离线模式，跳过"
    if NO_DIRECT:
        return "已开启中转且不允许直连：跳过直连测试"
    from quotes import Searcher
    r = Searcher().search("茅台")
    if not any(x["code"] == "sh600519" for x in r):
        raise AssertionError(f"搜索“茅台”没有返回 sh600519：{r}")
    r2 = Searcher().search("易方达蓝筹")
    fund = any(x["code"].startswith("jj") for x in r2)
    return f"茅台 → {len(r)} 条；场外基金搜索：{'有结果' if fund else '无结果（接口可能已调整）'}"


def main():
    print("盯盘小窗 自检\n" + "=" * 40)
    check("源码语法", t_syntax)
    check("模块导入", t_import)
    check("代码规范化", t_normalize)
    check("开盘时间判断", t_market_hours)
    check("搜索结果解析", t_parse_hint)
    check("中转连接串解析", t_parse_relay)
    check("公共根证书清单", t_ca_bundle)
    check("加密中转服务器（联网）", t_relay)
    check("直连行情接口（联网，https）", t_live_quotes)
    check("直连搜索接口（联网，https）", t_live_search)
    bad = 0
    for ok, name, msg in results:
        print(f"[{'通过' if ok else '失败'}] {name}" + (f"：{msg}" if msg else ""))
        bad += (not ok)
    print("=" * 40)
    print("全部通过" if not bad else f"{bad} 项失败，请把以上内容发给开发者")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
