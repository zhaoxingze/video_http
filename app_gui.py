#!/usr/bin/env python3
"""Desktop GUI for the webpage video downloader."""

from __future__ import annotations

import os
import queue
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from downloader import DownloadError, download_resolved_video, find_ffmpeg, resolve_url, sanitize_filename


APP_TITLE = "网页视频下载器"
VIDEO_SUFFIXES = {".mp4", ".m4v", ".mov", ".webm", ".flv", ".ts"}


def app_base_dir() -> Path:
    bundled = getattr(sys, "_MEIPASS", None)
    if bundled:
        return Path(bundled)
    return Path(__file__).resolve().parent


def resource_path(relative_path: str) -> Path:
    return app_base_dir() / relative_path


def default_download_dir() -> Path:
    downloads = Path.home() / "Downloads"
    if downloads.exists():
        return downloads
    return Path.cwd() / "downloads"


def default_font_spec() -> tuple[str, int]:
    return ("Microsoft YaHei UI", 10)


def ui_palette() -> dict[str, str]:
    return {
        "background": "#eef5ff",
        "surface": "#fbfdff",
        "surface_muted": "#f3f8ff",
        "primary": "#0078d7",
        "primary_dark": "#005faf",
        "accent": "#21b7d7",
        "text": "#121826",
        "muted": "#6b7280",
        "line": "#d7e2ee",
        "shadow": "#c9d8e8",
        "preview": "#18202b",
    }


def window_config() -> dict[str, object]:
    return {
        "geometry": "960x620",
        "minsize": (860, 560),
        "title_font": ("Microsoft YaHei UI", 24, "bold"),
        "subtitle_font": ("Microsoft YaHei UI", 12),
    }


def platform_badges() -> list[dict[str, str]]:
    return [
        {"text": "Y", "bg": "#ff0000", "fg": "#ffffff"},
        {"text": "B", "bg": "#fb7299", "fg": "#ffffff"},
        {"text": "D", "bg": "#111827", "fg": "#ffffff"},
        {"text": "V", "bg": "#20b9d8", "fg": "#ffffff"},
    ]


@dataclass(frozen=True)
class FieldSpec:
    label: str
    placeholder: str
    inline_help: str = ""


def field_specs() -> dict[str, FieldSpec]:
    return {
        "url": FieldSpec(
            label="网址链接",
            placeholder="请输入网址链接 (https://...)",
            inline_help="https://...",
        ),
        "output_dir": FieldSpec(
            label="保存目录",
            placeholder="选择或输入保存位置",
            inline_help="默认保存到系统下载文件夹。",
        ),
        "name": FieldSpec(label="文件名", placeholder="留空则使用视频标题"),
    }


def primary_button_options() -> dict[str, object]:
    palette = ui_palette()
    return {
        "bg": palette["primary"],
        "fg": "#ffffff",
        "activebackground": palette["primary_dark"],
        "activeforeground": "#ffffff",
        "disabledforeground": "#e0ecff",
        "font": ("Microsoft YaHei UI", 13, "bold"),
        "relief": tk.FLAT,
        "bd": 0,
        "cursor": "hand2",
        "highlightthickness": 0,
        "padx": 30,
        "pady": 18,
    }


def is_probable_url(value: str) -> bool:
    parsed = urlparse(value.strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def make_output_name(value: str) -> str | None:
    cleaned = value.strip()
    if not cleaned:
        return None
    for suffix in VIDEO_SUFFIXES:
        if cleaned.lower().endswith(suffix):
            cleaned = cleaned[: -len(suffix)]
            break
    return sanitize_filename(cleaned)


def format_size(size: int) -> str:
    units = ["B", "KB", "MB", "GB"]
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def format_finished_message(path: Path) -> str:
    size = path.stat().st_size if path.exists() else 0
    return f"下载完成：{path.name}\n大小：{format_size(size)}\n位置：{path.resolve()}"


class VideoDownloaderApp:
    def __init__(self, root: tk.Tk):
        palette = ui_palette()
        config = window_config()
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry(str(config["geometry"]))
        self.root.minsize(*config["minsize"])
        self.root.configure(bg=palette["background"])
        self.root.option_add("*Font", default_font_spec())

        self.url_var = tk.StringVar()
        self.output_dir_var = tk.StringVar(value=str(default_download_dir().resolve()))
        self.name_var = tk.StringVar()
        self.queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.worker: threading.Thread | None = None
        self.last_output_path: Path | None = None
        self.logo_image: tk.PhotoImage | None = None
        self.entry_placeholders: dict[tk.Entry, tuple[tk.StringVar, str]] = {}

        self._set_window_icon()
        self._configure_styles()
        self._build_ui()
        self._log("准备就绪。")
        self.root.after(150, self._poll_queue)

    def _set_window_icon(self) -> None:
        icon = resource_path("assets/app.ico")
        if icon.exists():
            try:
                self.root.iconbitmap(str(icon))
            except tk.TclError:
                pass

    def _configure_styles(self) -> None:
        palette = ui_palette()
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        config = window_config()
        style.configure("App.TFrame", background=palette["background"])
        style.configure("Header.TFrame", background=palette["background"])
        style.configure("Title.TLabel", background=palette["background"], foreground=palette["text"], font=config["title_font"])
        style.configure("Subtitle.TLabel", background=palette["background"], foreground=palette["muted"], font=config["subtitle_font"])
        style.configure("Horizontal.TProgressbar", troughcolor="#e4f5fb", background=palette["accent"], bordercolor="#e4f5fb")

    def _build_ui(self) -> None:
        palette = ui_palette()
        outer = ttk.Frame(self.root, style="App.TFrame", padding=(32, 24, 32, 28))
        outer.pack(fill=tk.BOTH, expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(1, weight=1)

        header = ttk.Frame(outer, style="Header.TFrame")
        header.grid(row=0, column=0, sticky=tk.EW, pady=(0, 22))
        header.columnconfigure(1, weight=1)

        logo_tile = tk.Frame(header, bg=palette["background"], width=82, height=82)
        logo_tile.grid(row=0, column=0, rowspan=2, sticky=tk.W, padx=(0, 18))
        logo_tile.grid_propagate(False)
        logo = self._load_logo()
        if logo is not None:
            tk.Label(logo_tile, image=logo, bg=palette["background"]).place(relx=0.5, rely=0.5, anchor=tk.CENTER)
        else:
            self._draw_logo_placeholder(logo_tile)
        ttk.Label(header, text=APP_TITLE, style="Title.TLabel").grid(row=0, column=1, sticky=tk.W)
        ttk.Label(header, text="粘贴链接，保存视频文件", style="Subtitle.TLabel").grid(row=1, column=1, sticky=tk.W, pady=(2, 0))

        settings_button = tk.Button(
            header,
            text="设置",
            command=self._show_settings_placeholder,
            bg=palette["background"],
            fg=palette["text"],
            activebackground="#e3efff",
            activeforeground=palette["text"],
            font=("Microsoft YaHei UI", 10),
            relief=tk.FLAT,
            bd=0,
            cursor="hand2",
            padx=12,
            pady=8,
        )
        settings_button.grid(row=0, column=2, rowspan=2, sticky=tk.NE, padx=(14, 0))

        shadow = tk.Frame(outer, bg=palette["shadow"], bd=0)
        shadow.grid(row=1, column=0, sticky=tk.NSEW)
        shadow.columnconfigure(0, weight=1)
        shadow.rowconfigure(0, weight=1)

        card = tk.Frame(shadow, bg=palette["surface"], bd=0, highlightthickness=1, highlightbackground="#dfe8f2")
        card.grid(row=0, column=0, sticky=tk.NSEW, padx=(0, 5), pady=(0, 5))
        card.columnconfigure(0, minsize=180)
        card.columnconfigure(1, weight=1)
        card.columnconfigure(2, minsize=190)
        card.rowconfigure(3, weight=1)

        specs = field_specs()
        preview = self._build_preview(card)
        preview.grid(row=0, column=0, rowspan=3, sticky=tk.NW, padx=(26, 18), pady=(32, 0))

        form = tk.Frame(card, bg=palette["surface"])
        form.grid(row=0, column=1, sticky=tk.NSEW, pady=(24, 0))
        form.columnconfigure(0, weight=1)
        self._add_field(form, 0, specs["url"], self.url_var, show_badges=True)
        self._add_field(form, 1, specs["output_dir"], self.output_dir_var, self._choose_output_dir)
        self._add_field(form, 2, specs["name"], self.name_var)

        action_panel = tk.Frame(card, bg=palette["surface"])
        action_panel.grid(row=0, column=2, rowspan=3, sticky=tk.NSEW, padx=(22, 28), pady=(58, 0))
        action_panel.columnconfigure(0, weight=1)

        self.download_button = tk.Button(action_panel, text="下载\n开始下载", command=self._start_download, **primary_button_options())
        self.download_button.grid(row=0, column=0, sticky=tk.EW, ipady=10)

        self.open_folder_button = tk.Button(
            action_panel,
            text="打开保存目录",
            command=self._open_output_folder,
            bg="#eef6ff",
            fg="#334155",
            activebackground="#dbeafe",
            activeforeground="#1f2937",
            font=("Microsoft YaHei UI", 10),
            relief=tk.FLAT,
            bd=0,
            cursor="hand2",
            highlightthickness=0,
            padx=14,
            pady=7,
        )
        self.open_folder_button.grid(row=1, column=0, sticky=tk.EW, pady=(18, 0))

        status_box = tk.Frame(card, bg=palette["surface"])
        status_box.grid(row=3, column=0, columnspan=3, sticky=tk.NSEW, padx=26, pady=(24, 22))
        status_box.columnconfigure(0, weight=1)
        status_box.rowconfigure(2, weight=1)

        tk.Label(
            status_box,
            text="下载状态",
            bg=palette["surface"],
            fg=palette["text"],
            font=("Microsoft YaHei UI", 10, "bold"),
        ).grid(row=0, column=0, sticky=tk.W, pady=(0, 8))

        self.progress = ttk.Progressbar(status_box, mode="indeterminate", style="Horizontal.TProgressbar")
        self.progress.grid(row=1, column=0, sticky=tk.EW, pady=(0, 10))

        status_shell = tk.Frame(status_box, bg=palette["surface_muted"], bd=0, highlightthickness=1, highlightbackground=palette["line"])
        status_shell.grid(row=2, column=0, sticky=tk.NSEW)
        status_shell.columnconfigure(0, weight=1)
        status_shell.rowconfigure(0, weight=1)

        self.status_text = tk.Text(
            status_shell,
            height=4,
            wrap=tk.WORD,
            relief=tk.FLAT,
            bg=palette["surface_muted"],
            fg=palette["text"],
            insertbackground=palette["text"],
            padx=12,
            pady=10,
            state=tk.DISABLED,
        )
        self.status_text.grid(row=0, column=0, sticky=tk.NSEW)

        scrollbar = ttk.Scrollbar(status_shell, command=self.status_text.yview)
        scrollbar.grid(row=0, column=1, sticky=tk.NS)
        self.status_text.configure(yscrollcommand=scrollbar.set)

    def _load_logo(self) -> tk.PhotoImage | None:
        image_path = resource_path("assets/app_icon.png")
        if not image_path.exists():
            return None
        try:
            self.logo_image = tk.PhotoImage(file=str(image_path))
            return self.logo_image
        except tk.TclError:
            return None

    def _draw_logo_placeholder(self, parent: tk.Widget) -> None:
        palette = ui_palette()
        canvas = tk.Canvas(parent, width=74, height=74, bg=palette["background"], highlightthickness=0)
        canvas.place(relx=0.5, rely=0.5, anchor=tk.CENTER)
        canvas.create_rectangle(7, 7, 67, 67, fill=palette["primary"], outline=palette["primary"])
        canvas.create_oval(19, 24, 48, 53, fill="#ffffff", outline="#ffffff")
        canvas.create_polygon(47, 28, 61, 37, 47, 46, fill="#7dd3fc", outline="#7dd3fc")

    def _build_preview(self, parent: tk.Widget) -> tk.Canvas:
        palette = ui_palette()
        canvas = tk.Canvas(parent, width=168, height=118, bg=palette["surface"], highlightthickness=0)
        canvas.create_rectangle(4, 4, 164, 112, fill=palette["preview"], outline="#0f172a")
        canvas.create_rectangle(4, 4, 164, 30, fill="#263241", outline="#263241")
        canvas.create_text(84, 18, text="视频预览", fill="#d7e5f2", font=("Microsoft YaHei UI", 9, "bold"))
        canvas.create_oval(61, 39, 107, 85, fill="#ffffff", outline="#ffffff")
        canvas.create_polygon(79, 51, 79, 73, 98, 62, fill=palette["primary"], outline=palette["primary"])
        canvas.create_rectangle(18, 94, 70, 100, fill="#334155", outline="#334155")
        canvas.create_rectangle(18, 102, 118, 106, fill="#1e293b", outline="#1e293b")
        return canvas

    def _show_settings_placeholder(self) -> None:
        messagebox.showinfo(APP_TITLE, "当前版本无需额外设置。")

    def _add_field(
        self,
        parent: tk.Widget,
        row: int,
        spec: FieldSpec,
        variable: tk.StringVar,
        button_command=None,
        show_badges: bool = False,
    ) -> None:
        palette = ui_palette()
        top_pad = 4 if row == 0 else 12
        field = tk.Frame(parent, bg=palette["surface"])
        field.grid(row=row, column=0, sticky=tk.EW, pady=(top_pad, 0))
        field.columnconfigure(0, weight=1)

        tk.Label(
            field,
            text=spec.label,
            bg=palette["surface"],
            fg=palette["text"],
            font=("Microsoft YaHei UI", 10, "bold"),
        ).grid(row=0, column=0, sticky=tk.W, pady=(0, 6))

        input_row = tk.Frame(field, bg=palette["surface"])
        input_row.grid(row=1, column=0, sticky=tk.EW)
        input_row.columnconfigure(0, weight=1)

        entry_shell = tk.Frame(input_row, bg="#ffffff", bd=0, highlightthickness=1, highlightbackground=palette["line"], highlightcolor=palette["accent"])
        entry_shell.grid(row=0, column=0, sticky=tk.EW)
        entry = tk.Entry(
            entry_shell,
            textvariable=variable,
            bg="#ffffff",
            fg=palette["text"],
            insertbackground=palette["text"],
            relief=tk.FLAT,
            bd=0,
            highlightthickness=0,
            font=default_font_spec(),
        )
        entry.pack(fill=tk.X, padx=12, pady=9)
        self._install_placeholder(entry, variable, spec.placeholder)

        if button_command is not None:
            tk.Button(
                input_row,
                text="浏览",
                command=button_command,
                bg="#eef6ff",
                fg="#334155",
                activebackground="#dbeafe",
                activeforeground="#1f2937",
                font=("Microsoft YaHei UI", 10),
                relief=tk.FLAT,
                bd=0,
                cursor="hand2",
                highlightthickness=0,
                padx=14,
                pady=9,
            ).grid(row=0, column=1, sticky=tk.E, padx=(10, 0))
        elif show_badges:
            badge_row = tk.Frame(input_row, bg=palette["surface"])
            badge_row.grid(row=0, column=1, sticky=tk.E, padx=(10, 0))
            self._add_platform_badges(badge_row)
        if spec.inline_help:
            tk.Label(
                field,
                text=spec.inline_help,
                bg=palette["surface"],
                fg=palette["muted"],
                font=("Microsoft YaHei UI", 9),
            ).grid(row=2, column=0, sticky=tk.W, pady=(6, 0))

    def _add_platform_badges(self, parent: tk.Widget) -> None:
        for column, badge in enumerate(platform_badges()):
            tk.Label(
                parent,
                text=badge["text"],
                bg=badge["bg"],
                fg=badge["fg"],
                font=("Microsoft YaHei UI", 9, "bold"),
                width=2,
                height=1,
            ).grid(row=0, column=column, padx=(0 if column == 0 else 5, 0))

    def _install_placeholder(self, entry: tk.Entry, variable: tk.StringVar, placeholder: str) -> None:
        if not placeholder:
            return

        def show_placeholder() -> None:
            if not variable.get():
                entry.configure(fg="#94a3b8")
                variable.set(placeholder)

        def hide_placeholder(_event=None) -> None:
            if variable.get() == placeholder:
                variable.set("")
            entry.configure(fg="#243044")

        def restore_placeholder(_event=None) -> None:
            if variable.get():
                entry.configure(fg="#243044")
            else:
                show_placeholder()

        entry.bind("<FocusIn>", hide_placeholder)
        entry.bind("<FocusOut>", restore_placeholder)
        self.entry_placeholders[entry] = (variable, placeholder)
        show_placeholder()

    @staticmethod
    def _value_without_placeholder(variable: tk.StringVar, placeholder: str) -> str:
        value = variable.get().strip()
        if value == placeholder:
            return ""
        return value

    def _choose_output_dir(self) -> None:
        selected = filedialog.askdirectory(initialdir=self.output_dir_var.get() or str(default_download_dir()))
        if selected:
            self.output_dir_var.set(selected)

    def _start_download(self) -> None:
        specs = field_specs()
        url = self._value_without_placeholder(self.url_var, specs["url"].placeholder)
        if not is_probable_url(url):
            messagebox.showwarning(APP_TITLE, "请先输入 http 或 https 开头的网址链接。")
            return

        output_dir_value = self._value_without_placeholder(self.output_dir_var, specs["output_dir"].placeholder)
        output_name_value = self._value_without_placeholder(self.name_var, specs["name"].placeholder)
        output_dir = Path(output_dir_value or default_download_dir())
        output_name = make_output_name(output_name_value)

        self.last_output_path = None
        self._set_download_button_busy(True)
        self.progress.start(12)
        self._clear_log()
        self._log("正在解析网页和视频源...")
        self.worker = threading.Thread(
            target=self._download_worker,
            args=(url, output_dir, output_name),
            daemon=True,
        )
        self.worker.start()

    def _download_worker(self, url: str, output_dir: Path, output_name: str | None) -> None:
        try:
            video = resolve_url(url)
            self.queue.put(("status", f"已找到视频源：{video.source}\n类型：{video.kind}\n正在下载..."))
            output_path = download_resolved_video(video, output_dir, output_name)
            self.queue.put(("success", output_path))
        except Exception as exc:
            self.queue.put(("error", exc))

    def _poll_queue(self) -> None:
        try:
            while True:
                kind, payload = self.queue.get_nowait()
                if kind == "status":
                    self._log(str(payload))
                elif kind == "success":
                    self._finish_success(Path(payload))
                elif kind == "error":
                    self._finish_error(payload)
        except queue.Empty:
            pass
        self.root.after(150, self._poll_queue)

    def _finish_success(self, path: Path) -> None:
        self.progress.stop()
        self._set_download_button_busy(False)
        self.last_output_path = path
        message = format_finished_message(path)
        self._log(message)
        messagebox.showinfo(APP_TITLE, message)

    def _finish_error(self, error: object) -> None:
        self.progress.stop()
        self._set_download_button_busy(False)
        if isinstance(error, DownloadError):
            message = str(error)
        else:
            message = f"{type(error).__name__}: {error}"
        self._log(f"下载失败：{message}")
        messagebox.showerror(APP_TITLE, f"下载失败：\n{message}")

    def _set_download_button_busy(self, busy: bool) -> None:
        if busy:
            self.download_button.configure(
                text="下载中...\n请稍候",
                state=tk.DISABLED,
                bg="#93c5fd",
                activebackground="#93c5fd",
            )
            return

        options = primary_button_options()
        self.download_button.configure(
            text="下载\n开始下载",
            state=tk.NORMAL,
            bg=options["bg"],
            activebackground=options["activebackground"],
        )

    def _open_output_folder(self) -> None:
        specs = field_specs()
        output_dir_value = self._value_without_placeholder(self.output_dir_var, specs["output_dir"].placeholder)
        target = self.last_output_path.parent if self.last_output_path else Path(output_dir_value or default_download_dir())
        target.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(target))
        except OSError as exc:
            messagebox.showerror(APP_TITLE, f"无法打开目录：\n{exc}")

    def _clear_log(self) -> None:
        self.status_text.configure(state=tk.NORMAL)
        self.status_text.delete("1.0", tk.END)
        self.status_text.configure(state=tk.DISABLED)

    def _log(self, message: str) -> None:
        self.status_text.configure(state=tk.NORMAL)
        if self.status_text.index("end-1c") != "1.0":
            self.status_text.insert(tk.END, "\n\n")
        self.status_text.insert(tk.END, message)
        self.status_text.see(tk.END)
        self.status_text.configure(state=tk.DISABLED)


def smoke_test() -> int:
    assert is_probable_url("https://example.com")
    assert make_output_name("a:b.mp4") == "a_b"
    assert default_download_dir()
    assert resource_path("assets/app.ico").exists()
    return 0


def platform_runtime_smoke_diagnostics() -> str:
    try:
        import yt_dlp  # type: ignore
        from yt_dlp import YoutubeDL  # type: ignore
    except Exception as exc:
        raise RuntimeError(f"Platform runtime missing yt-dlp: {exc}") from exc

    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("Platform runtime missing ffmpeg executable path.")

    ffmpeg_path = Path(ffmpeg)
    if not ffmpeg_path.exists() or not ffmpeg_path.is_file():
        raise RuntimeError(f"Platform runtime ffmpeg path not found: {ffmpeg}")

    version_text = getattr(yt_dlp, "__version__", None)
    if not version_text:
        version_module = getattr(yt_dlp, "version", None)
        version_text = getattr(version_module, "__version__", None)
    if not version_text:
        version_text = "unknown"

    _ = YoutubeDL
    return f"yt-dlp={version_text}\nffmpeg={ffmpeg_path.resolve()}"


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if "--smoke-test" in argv:
        return smoke_test()
    for index, arg in enumerate(argv):
        if arg == "--smoke-test-file" and index + 1 < len(argv):
            result = smoke_test()
            Path(argv[index + 1]).write_text("ok", encoding="utf-8")
            return result
        if arg.startswith("--smoke-test-file="):
            result = smoke_test()
            Path(arg.split("=", 1)[1]).write_text("ok", encoding="utf-8")
            return result
        if arg == "--platform-smoke-test-file" and index + 1 < len(argv):
            diagnostics = platform_runtime_smoke_diagnostics()
            Path(argv[index + 1]).write_text(diagnostics, encoding="utf-8")
            return 0
        if arg.startswith("--platform-smoke-test-file="):
            diagnostics = platform_runtime_smoke_diagnostics()
            Path(arg.split("=", 1)[1]).write_text(diagnostics, encoding="utf-8")
            return 0

    root = tk.Tk()
    VideoDownloaderApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
