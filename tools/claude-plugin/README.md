# Claude 开发辅助插件：stock-monitor-dev

让 Claude（桌面版 Cowork 会话）每次编辑 `.py` 文件后，自动在**本机**检查代码：

1. 对被编辑的文件做语法检查
2. 运行 `selftest.py` 的离线部分（语法、模块导入、代码解析、开盘时间、连接串解析；不访问网络）

未通过时，错误信息会自动反馈给 Claude，Claude 会接着修复。hook 运行在你的 Windows 电脑上，需要本机已安装 Python 并在 PATH 中。

## 安装

1. 把 `stock-monitor-dev` 文件夹压缩成 zip（右键 → 发送到 → 压缩(zipped)文件夹），
   zip 里第一层应是 `.claude-plugin/` 和 `hooks/`
2. Claude 桌面版 → **Customize → Plugins** → 上传插件，选择这个 zip
3. 新开一个会话后生效

如果找不到上传入口，可能是管理员关闭了用户上传插件（`userPluginUploadsEnabled`）。

## 文件

| 文件 | 说明 |
|---|---|
| `.claude-plugin/plugin.json` | 插件信息 |
| `hooks/hooks.json` | 在 Edit / Write 之后触发 |
| `hooks/check_after_edit.py` | 检查脚本（只用 Python 标准库） |
