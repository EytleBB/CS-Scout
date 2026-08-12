# CS-Scout v2.1.1-alpha.4 发布说明

这是 2.1.1 的第四个公开测试版，重点修复 5E 自动侦察恢复流程和特殊 Windows 管理员环境的安装兼容性。

## 本次修复

- 5E 已经以普通方式运行、没有开放自动侦察端口时，页面会等待用户正常退出 5E，再进行一次受控自动重启。
- 自动重启增加短暂退避和一次上限；若新的 5E 仍未开放端口，会停止自动重试并明确提示选择 5E 或切换手动模式，不会循环弹出 UAC。
- 重新进入自动模式或重新选择 `5EClient.exe` 后，可以再次尝试自动侦察。
- 只有内置 `Administrator` 账号，或关闭 UAC/管理员审批模式的 Windows 电脑，现在可以正常安装和启动 CS-Scout。
- 普通管理员账号主动选择“以管理员身份运行”仍会被阻止，避免误用提升后的环境。
- 安装器和启动器共用同一套 Windows 令牌分类逻辑；数据始终保存在当前登录账号的 `%LOCALAPPDATA%\CS-Scout`。

## Windows 下载与更新

下载以下三个资产：

```text
CS-Scout-Windows-x64-v2.1.1-alpha.4.zip
CS-Scout-Windows-x64-v2.1.1-alpha.4.zip.sha256
SHA256SUMS.txt
```

不要下载 GitHub 自动生成的 `Source code` 压缩包。停止旧版，把 Alpha 4 完整解压到新目录，
然后依次双击：

```text
windows\Install-CS-Scout.cmd
windows\Start-CS-Scout.cmd
```

不要覆盖 Alpha 3 的旧目录。Demo 缓存和分析结果保存在 `%LOCALAPPDATA%\CS-Scout`，更换程序目录不会丢失。

## 使用提醒

- 5E 自动模式提示“已启动但未开放自动侦察”时，请正常退出 5E，包括托盘中的客户端；保持 CS-Scout 运行即可。
- CS-Scout 不会强制结束已经运行的 5E，也不会无限重启客户端；自动恢复失败时可继续使用 5E 手动模式。
- 内置 Administrator 或关闭 UAC 的系统会在安装和启动时显示兼容性警告，这是预期行为。
- 普通 Windows 账号请直接双击运行，不要主动选择“以管理员身份运行”。

## 验证

- 新增 5E“普通启动无 CDP → 用户退出 → 延迟重启一次”状态机测试。
- 新增重启失败不循环、重新进入自动模式可恢复的回归测试。
- Windows 包验证覆盖标准/过滤令牌、主动提升令牌、内置 Administrator 和关闭 UAC 的默认完整令牌。
- 全量自动测试 259 项通过。
- Windows 发布包继续执行严格文件白名单、PowerShell 5.1 语法、依赖、地图资源、敏感文件和 ZIP 清单检查。
- 发布资产提供独立 SHA-256 校验文件。
