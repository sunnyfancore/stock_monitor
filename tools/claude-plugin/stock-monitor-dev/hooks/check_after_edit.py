#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PostToolUse hook：Claude 编辑 .py 文件后，在本机自动检查

1. 对被编辑的 .py 文件做语法编译（任何 .py 都检查，包括 server/relay.py）
2. 若该文件属于盯盘小窗项目（同目录或上级目录有 app.py + selftest.py），再运行 selftest.py 离线部分
失败时以退出码 2 结束，并把错误写到 stderr —— Claude 会收到这些信息并继续修复；
与本项目无关的文件直接放行（退出码 0）。只用 Python 标准库。
"""
import json
import os
import subprocess
import sys


def find_project_root(path, levels=3):
    d = os.path.dirname(os.path.abspath(path))
    for _ in range(levels):
        if os.path.exists(os.path.join(d, "selftest.py")) and os.path.exists(os.path.join(d, "app.py")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return None


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0
    tool_input = data.get("tool_input") or {}
    path = tool_input.get("file_path") or tool_input.get("path") or ""
    if not path.lower().endswith(".py") or not os.path.exists(path):
        return 0

    # 1) 语法检查（只编译不落盘，不在项目里产生 .pyc）
    try:
        with open(path, "rb") as f:
            compile(f.read(), path, "exec")
    except SyntaxError as e:
        print(f"语法错误：{os.path.basename(path)} 第 {e.lineno} 行：{e.msg}\n{(e.text or '').rstrip()}",
              file=sys.stderr)
        return 2

    # 2) 项目自检（离线部分：语法、导入、代码解析、开盘时间、连接串解析……不访问任何网络）
    root = find_project_root(path)
    if not root:
        return 0
    env = dict(os.environ, SELFTEST_OFFLINE="1", PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    try:
        r = subprocess.run([sys.executable, "selftest.py"], cwd=root, env=env,
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=80)
    except subprocess.TimeoutExpired:
        print("本机自检超时（80 秒），请检查是否有阻塞的代码", file=sys.stderr)
        return 2
    output = ((r.stdout or "") + (r.stderr or "")).strip()
    if r.returncode != 0:
        print("本机自检未通过（hook 自动运行 selftest.py 离线部分）：\n" + output[-4000:],
              file=sys.stderr)
        return 2
    print("本机自检通过：" + (output.splitlines()[-1] if output else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
