# 网页视频下载工具

这个小程序读取一个网址链接，并尽量把页面中的视频下载成普通文件。

优先级：

1. 页面里有“下载 / download / save”等按钮或链接时，优先直接下载这个链接。
2. 没有下载按钮时，查找 `<video>`、`<source>`、`.mp4`、`.m3u8` 等媒体地址。
3. 对央视网这类没有下载按钮、但页面里有 `videoCenterId` 的播放器页面，自动调用央视公开视频信息接口，解析 HLS 清单，再封装成 MP4。

## 安装

进入本目录：

```powershell
cd F:\photoshop\video_downloader
```

安装 HLS 转 MP4 所需的 ffmpeg 包：

```powershell
python -m pip install -r requirements.txt
```

如果你的电脑已经安装了系统版 `ffmpeg`，也可以不装上面的包。

## 使用

下载网页里的视频：

```powershell
python downloader.py "https://news.cctv.com/2024/04/04/ARTI0WWu5RkdJ8da9TTsm2DX240404.shtml"
```

指定保存目录：

```powershell
python downloader.py "https://example.com/page-with-video.html" -o F:\photoshop\downloads
```

指定文件名：

```powershell
python downloader.py "https://example.com/page-with-video.html" -n my_video
```

只解析真实视频源，不下载：

```powershell
python downloader.py "https://example.com/page-with-video.html" --dry-run
```

## 双击版 App

已经提供窗口版入口 `app_gui.py`，并带有应用图标。重新打包 Windows `.exe` 时运行：

```powershell
.\build_app.ps1
```

生成文件：

```text
dist\VideoDownloaderApp.exe
```

双击这个文件，粘贴网址，选择保存目录，然后点击“开始下载”即可。

当前也已经复制了一份到桌面：

```text
C:\Users\zhao'xing'ze\Desktop\网页视频下载器.exe
```

这是单文件 App，复制到桌面或其它文件夹也可以打开，不需要把源码目录一起复制。

## 运行测试

```powershell
python -m unittest -v test_downloader.py test_app_gui.py
```

## 说明

这个工具优先按普通网页规则解析页面里的下载按钮、`<video>`、`<source>`、`.mp4`、`.m3u8` 等直链；只有遇到不支持或平台型页面时，才回退到 `yt-dlp` 处理。

像 Bilibili 这类常见平台如果返回 DASH 视频流和音频流，程序会通过随 App 一起打包的 FFmpeg 自动合并，生成可直接分享的普通视频文件。

这个工具只支持网页公开提供、浏览器能直接访问到的内容；不会自动读取浏览器 cookies，也不会绕过登录、会员、付费墙、地区限制、DRM 或平台明确限制下载的保护。遇到这些情况时，程序会给出错误提示。
