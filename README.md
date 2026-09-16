# Streamforge · 流铸

本地 yt-dlp 媒体流下载控制台。支持解析实际视频/音频流、手动选择 format、直接合并、任务队列和本地配置持久化。

## 当前状态

第一版后端已接入：

- `POST /api/inspect`：调用 yt-dlp 返回实际格式 JSON
- `POST /api/jobs`：创建下载任务，使用 ffmpeg 做必要的无损合并
- `GET /api/jobs`：查看任务状态
- `DELETE /api/jobs/<id>`：中断或取消任务
- `GET/POST /api/config`：保存并读取并发数、Cookie、代理和默认行为
- 静态页面由同一个 Python 服务提供

B站扫码登录、插件运行时 API、项目自更新和 yt-dlp 在线更新的界面已经预留，但尚未接入后端。安装或恢复插件后是否需要重启，当前以界面提示为准，实际插件加载器会在后续版本实现。

## 安装

在宿主机终端运行：

```sh
chmod +x install.sh
./install.sh
```

安装脚本会检测宿主机 Node.js：

- 选择使用宿主机 Node.js：以只读方式挂载，不改宿主机文件。
- 选择不使用后，会继续询问是否下载当前架构的 Node.js 到 `data/tools/node/`。
- 两项都不选择：仍可使用基础下载功能，但 YouTube 某些解析可能受限。

安装选择会写入生成的 `compose.generated.yaml`。Node.js 来源和版本可通过 `/api/health` 查看。

安装和更新容器可能中断正在进行的任务；被中断的任务在服务重启后会重新排队。配置、下载目录和插件目录使用独立挂载保存。

## 本地自测

```sh
python3 test_media_files.py   # 元数据、NFO、封面命名
python3 test_media_auth.py    # Cookie 往返、账号状态、扫码状态映射
python3 test_plugin_zip.py    # 插件 ZIP 的安全边界与安装回退
python3 test_jobs.py          # 任务状态与 jobs.json 的容错
```

四个脚本都不联网：把数据目录指到临时目录，只执行 `server.py` 的函数体（截到起线程之前），不起 HTTP 服务、不下载。失败时退出码非零。

CI 里由 `Tests` 工作流执行，镜像构建（`container.yml`、`release-image.yml`）依赖它先通过。

## Preview

直接打开 `index.html` 可以查看页面，但要使用真实解析和下载，请通过上述服务访问。

## Repository

<https://github.com/molakesizhanfangguangmang/streamforge>
