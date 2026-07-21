# CS-Scout v2.0.2 发布说明

这个版本合并了 v2.0.1 的 Windows 云电脑兼容修复与新的公开回放查看方式。

## 主要变化

- Windows 玩家版继续由系统自动选择空闲本地端口，不会要求结束占用 5000 端口的远程控制程序。
- 网页打开后会自动读取当前进度和最近一次分析结果，不再要求先输入密钥。
- `/api/status`、`/api/results`、玩家回放 JSON 和输出 JSON 现在可公开读取。
- 分析密钥仅用于启动新的 Demo 下载与解析任务，仍不会写入浏览器持久存储。
- 服务重启后，网页会从 `analysis_summary.json` 恢复并展示最近一次分析结果。
- 页面会持续轮询公开状态；检测到新任务时会清理旧回放，避免混合两次分析。

## 权限说明

公开部署意味着任何能访问站点的人都可以看到分析进度和已生成的回放结果，但只有持有分析密钥的人可以启动新任务。若回放结果也必须只对少数人可见，请不要升级到这个版本，或在 Nginx/VPN 层增加额外访问限制。

## 下载与安装

下载并完整解压：

```text
CS-Scout-Windows-x64-v2.0.2.zip
```

然后依次双击：

1. `windows\Install-CS-Scout.cmd`
2. `windows\Start-CS-Scout.cmd`

现有密钥、Demo 缓存和分析输出仍保存在 `%LOCALAPPDATA%\CS-Scout`，升级不会删除这些数据。

## 完整性校验

```powershell
Get-FileHash -Algorithm SHA256 .\CS-Scout-Windows-x64-v2.0.2.zip
```

结果必须与发布页的 `SHA256SUMS.txt` 完全一致。
