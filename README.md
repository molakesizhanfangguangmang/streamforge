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

## Docker

```sh
docker compose up -d --build
```

默认访问 `http://127.0.0.1:8081`。数据写入 `./data`，下载写入 `./downloads`。容器镜像包含 `ffmpeg`、`node` 和最新稳定版 yt-dlp。

## Preview

直接打开 `index.html` 可以查看页面，但要使用真实解析和下载，请通过上述服务访问。

## Repository

<https://github.com/molakesizhanfangguangmang/streamforge>
