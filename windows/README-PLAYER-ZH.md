# CS-Scout Windows 玩家版使用说明

这个版本让每位玩家在自己的 Windows 电脑上运行 CS-Scout。程序只监听
`127.0.0.1`，由 Windows 自动分配空闲端口；局域网和互联网中的其他设备无法连接，
不需要开放防火墙端口。

## 一、准备电脑

- Windows 10 或 Windows 11，64 位系统。
- 建议至少保留 16 GB 可用磁盘空间；Demo 会保存在本机并占用较多空间。
- 安装 64 位 Python 3.11 或 3.12：<https://www.python.org/downloads/windows/>。
  安装 Python 时建议保留 **Python Launcher** 选项。
- 安装和分析 Demo 都需要联网。

## 二、下载和安装

1. 在 GitHub Releases 页面下载名称类似
   `CS-Scout-Windows-x64-v2.0.2.zip` 的 Windows 发布包。
2. 同时下载 `SHA256SUMS.txt`，在 ZIP 所在目录运行
   `Get-FileHash -Algorithm SHA256 .\CS-Scout-Windows-x64-v2.0.2.zip`，确认结果与
   校验文件中的 64 位散列完全相同；不同就不要运行。
3. 不要下载 GitHub 自动生成的 `Source code (zip)` 或 `Source code (tar.gz)`；它们可能不含雷达地图。
4. 右键 ZIP，选择“全部解压”。不要直接在压缩包预览窗口里运行程序。
5. 打开解压后的 `windows` 文件夹，双击 `Install-CS-Scout.cmd`。
6. 第一次安装需要下载 Python 依赖，通常需要几分钟。看到
   `Installation completed successfully` 表示完成。

请普通双击运行，不要选择“以管理员身份运行”；否则数据会进入错误的 Windows 用户目录。

安装程序不会请求管理员权限，也不会修改系统防火墙。它会：

- 在发布包内创建独立的 `.venv` Python 环境；
- 在 `%LOCALAPPDATA%\CS-Scout` 创建运行数据目录；
- 首次生成 64 位随机访问密钥，后续安装和升级会继续使用同一个密钥。

## 三、启动

1. 双击 `windows\Start-CS-Scout.cmd`。
2. 启动窗口会显示本次使用的 `Address`，并自动打开这个地址。端口由 Windows 分配，
   所以不同电脑或不同启动次数显示的数字可能不同，这是正常现象。
3. 分析密钥会自动复制到剪贴板；查看已有结果不需要密钥，开始新分析前在网页密钥框按 `Ctrl+V` 粘贴。
4. 第一次使用建议选择“普通”模式、一名玩家和一个 Demo 进行测试。
5. 使用期间保持黑色启动窗口打开。

如果 Windows 防火墙首次弹出“允许访问”提示，可以选择“取消”或不允许；本机
`127.0.0.1` 使用不需要开放公用网络或专用网络入站权限。

不要在分析任务运行时关闭黑色窗口。没有任务时，可按 `Ctrl+C` 或关闭窗口停止本机服务。

如果之后需要重新复制密钥，双击 `windows\Copy-Access-Key.cmd`。不要把密钥发给陌生人，
也不要把 `%LOCALAPPDATA%\CS-Scout\secret.key` 上传到网盘或 GitHub。

## 四、文件保存位置

这些目录不会放进发布包，升级程序时可以继续保留：

```text
%LOCALAPPDATA%\CS-Scout\
├─ secret.key    访问密钥
├─ demos\        已下载的 Demo 缓存
└─ output\       最近分析生成的 JSON
```

需要释放磁盘时，先停止 CS-Scout，再删除 `demos` 目录中的缓存文件。不要在分析过程中删除文件。

## 五、升级

1. 等待当前分析完成，关闭旧版本启动窗口。
2. 下载并“全部解压”新的 Windows 发布包到新目录。
3. 双击新版本的 `windows\Install-CS-Scout.cmd`。
4. 安装成功后从新版本运行 `windows\Start-CS-Scout.cmd`。

密钥、Demo 缓存和输出位于 `%LOCALAPPDATA%\CS-Scout`，不会因为更换发布包而丢失。
确认新版本可用后，可以删除旧版本解压目录。

## 六、常见问题

### 提示找不到 Python 3.11 或 3.12

安装 Python 3.11/3.12 64 位版本，然后重新运行安装脚本。如果 Windows 跳转到 Microsoft
Store，请在 Windows“管理应用执行别名”中关闭 `python.exe` 的商店别名，或重新安装
python.org 提供的版本并保留 Python Launcher。

### 其他程序正在使用 5000 端口

不需要关闭或结束那个程序。CS-Scout 会让 Windows 原子分配另一个空闲本地端口，
并在启动窗口的 `Address` 后显示实际地址。启动器不会结束不认识的进程。

### 网页没有自动打开

保持启动窗口打开，把窗口中 `Address` 后面的完整地址复制到浏览器。

### 提示可用磁盘不足 16 GB

这是容量提醒，不是启动失败。建议先使用“普通”模式、1 名玩家和 1–2 个 Demo。
如果可用空间低于 9 GB，启动器会停止运行；需要释放空间后再试。停止 CS-Scout 后可以
删除 `%LOCALAPPDATA%\CS-Scout\demos`，其中的索引文件之后会自动重建。

### 提示无法收紧访问密钥 ACL

某些远程云电脑不允许应用修改文件权限。这条警告不会阻止本机使用；密钥仍保存在当前
用户的 `%LOCALAPPDATA%\CS-Scout\secret.key`。不要共享或上传这个文件。

### 开始分析时提示密钥错误

双击 `windows\Copy-Access-Key.cmd`，回到网页重新粘贴。不要输入服务器版本使用的共享密钥；
每台玩家电脑都有自己的本地密钥。

### 安装依赖失败

检查网络、代理或安全软件后，再次运行 `Install-CS-Scout.cmd`。安装脚本是幂等的，重复运行
不会删除密钥、Demo 或输出。

## 七、卸载

先停止 CS-Scout，然后删除解压出来的发布包目录即可删除程序。如果还要彻底删除密钥、
Demo 和分析结果，再手动删除 `%LOCALAPPDATA%\CS-Scout`。此操作无法恢复，请先确认没有需要保留的数据。
