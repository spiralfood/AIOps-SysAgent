import tkinter as tk
import tkinter.font as tkfont
import tkinter.messagebox as messagebox
import customtkinter as ctk
import threading
import queue
import time
import sqlite3
import datetime
import re
import math
import psutil
import requests
import json
from collections import deque
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from config import DEEPSEEK_API_KEY, DEEPSEEK_API_URL
from utils import style_axis_light, normalize_top_processes_payload
from history_window import HistoryInsightWindow

class SystemMonitorApp:
    def __init__(self, root):
        self.root = root
        self.root.title("系统资源监控与 AI 诊断面板")
        self.root.geometry("800x600")
        self.root.configure(fg_color="#F3F3F3")
        
        # 核心数据结构：最多保存最近 60 秒的数据
        self.max_points = 60
        self.cpu_data = deque([0]*self.max_points, maxlen=self.max_points)
        self.mem_data = deque([0]*self.max_points, maxlen=self.max_points)
        self.x_data = list(range(self.max_points))
        
        # 线程通信队列
        self.data_queue = queue.Queue() # 用于监控数据
        self.top_process_queue = queue.Queue() # 用于 Top 进程数据
        self.ai_queue = queue.Queue()   # 用于 AI 流式文本数据
        self.is_diagnosing = False      # 状态锁
        self.last_db_write_time = 0
        self.raw_ai_text = ""
        self.top_process_count = 10
        self.top_process_interval = 2.0
        self.last_top_process_time = 0.0
        self.latest_top_processes = {"cpu": [], "mem": []}

        self.init_db()
        self.setup_ui()
        self.start_worker_thread()
        self.start_ui_update_loop()
        self.start_top_process_update_loop()
        self.start_ai_update_loop()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def init_db(self):
        """初始化 SQLite 并建表。"""
        self.conn = sqlite3.connect("system_log.db", check_same_thread=False)
        self.cursor = self.conn.cursor()
        self.cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS resource_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL,
                cpu_percent REAL,
                mem_percent REAL
            )
            """
        )

        # 平滑迁移：若旧库缺少 top_processes 列，则增量补齐。
        self.cursor.execute("PRAGMA table_info(resource_log)")
        columns = [row[1] for row in self.cursor.fetchall()]
        if "top_processes" not in columns:
            self.cursor.execute("ALTER TABLE resource_log ADD COLUMN top_processes TEXT")

        self.conn.commit()
        self.last_db_write_time = time.time()

    def setup_ui(self):
        """初始化 UI，按 7:3 比例分割左右区域"""
        # 主容器
        main_frame = ctk.CTkFrame(self.root, fg_color="#F9F9F9")
        main_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)

        # -- 左侧图表区 (70% 宽度) --
        left_frame = ctk.CTkFrame(main_frame, fg_color="#FFFFFF")
        left_frame.place(relx=0, rely=0, relwidth=0.7, relheight=1.0)

        # 初始化 Matplotlib Figure 和子图
        self.fig = Figure(figsize=(5, 5), dpi=100)
        self.fig.patch.set_facecolor("#FFFFFF")
        # 3行1列：CPU、内存、Top进程
        self.ax_cpu = self.fig.add_subplot(3, 1, 1)
        self.ax_mem = self.fig.add_subplot(3, 1, 2)
        self.ax_proc = self.fig.add_subplot(3, 1, 3)

        # 设置图表样式与范围
        self.ax_cpu.set_title("CPU 使用率 (%)")
        self.ax_cpu.set_ylim(0, 100)
        style_axis_light(self.ax_cpu)
        
        self.ax_mem.set_title("内存 使用率 (%)")
        self.ax_mem.set_ylim(0, 100)
        style_axis_light(self.ax_mem)

        self.ax_proc.set_title("Top 进程占用 (%)")
        self.ax_proc.set_xlabel("占用率 (%)")
        style_axis_light(self.ax_proc)
        self.ax_proc.grid(False, axis='y')
        
        # 隐藏 X 轴刻度，让它看起来像一个滚动的示波器
        self.ax_cpu.set_xticks([])
        self.ax_mem.set_xticks([])

        # 初始化线条
        self.line_cpu, = self.ax_cpu.plot(self.x_data, self.cpu_data, color='#005FB8', linewidth=1.8, animated=False)
        self.line_mem, = self.ax_mem.plot(self.x_data, self.mem_data, color='#107C10', linewidth=1.8, animated=False)
        self.render_top_process_chart([])
        self.fig.tight_layout()

        # 将 Matplotlib Figure 嵌入到 Tkinter 
        self.canvas = FigureCanvasTkAgg(self.fig, master=left_frame)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

        # -- 右侧 AI 诊断区 (30% 宽度) --
        right_frame = ctk.CTkFrame(main_frame, fg_color="#FFFFFF")
        right_frame.place(relx=0.72, rely=0, relwidth=0.28, relheight=1.0) # 留出0.02的间距

        # 诊断按钮
        self.btn_diagnose = ctk.CTkButton(
            right_frame, 
            text="一键生成 AI 深度诊断报告", 
            command=self.on_diagnose_click
        )
        self.btn_diagnose.pack(fill=tk.X, pady=(10, 10), ipady=8)

        self.btn_history = ctk.CTkButton(
            right_frame,
            text="查看历史负载趋势",
            command=self.on_history_click
        )
        self.btn_history.pack(fill=tk.X, pady=(0, 10), ipady=8)

        # AI 文本输出框
        ctk.CTkLabel(right_frame, text="AI 诊断输出：", anchor="w").pack(fill=tk.X, padx=2, pady=(0, 5))
        self.text_ai = ctk.CTkTextbox(right_frame, wrap="word", font=("微软雅黑", 11))
        self.text_ai.pack(fill=tk.BOTH, expand=True)
        self.main_font_size = 11
        self.main_font_min = 10
        self.main_font_max = 24
        self.md_bold_font = tkfont.Font(family="微软雅黑", size=self.main_font_size, weight="bold")
        self.md_h3_font = tkfont.Font(family="微软雅黑", size=self.main_font_size + 4, weight="bold")
        self.main_chat_head_font = tkfont.Font(family="微软雅黑", size=max(9, self.main_font_size - 1), weight="bold")
        self.main_user_font = tkfont.Font(family="微软雅黑", size=self.main_font_size, weight="bold")
        self.main_agent_font = tkfont.Font(family="微软雅黑", size=self.main_font_size)
        # CTkTextbox 限制 tag_config 的 font 参数，需要对底层 Tk Text 控件配置标签。
        text_widget = self.text_ai._textbox
        text_widget.tag_configure("md_bold", font=self.md_bold_font)
        text_widget.tag_configure("md_h3", font=self.md_h3_font, foreground="#9ad1ff")
        text_widget.tag_configure("main_user_head", foreground="#2F80ED", font=self.main_chat_head_font, lmargin1=96, lmargin2=96, rmargin=14, justify="right", spacing1=8, spacing3=3)
        text_widget.tag_configure("main_agent_head", foreground="#6FA8DC", font=self.main_chat_head_font, lmargin1=14, lmargin2=14, rmargin=96, justify="left", spacing1=8, spacing3=3)
        text_widget.tag_configure("main_user", foreground="#0B3D6E", background="#EAF3FF", font=self.main_user_font, lmargin1=96, lmargin2=96, rmargin=14, justify="right", spacing1=2, spacing3=8)
        text_widget.tag_configure("main_agent", foreground="#222222", background="#F5F7FA", font=self.main_agent_font, lmargin1=14, lmargin2=14, rmargin=96, justify="left", spacing1=2, spacing3=8)
        text_widget.tag_configure("main_user_bold", foreground="#0B3D6E", background="#EAF3FF", font=self.md_bold_font, lmargin1=96, lmargin2=96, rmargin=14)
        text_widget.tag_configure("main_agent_bold", foreground="#1F2937", background="#F5F7FA", font=self.md_bold_font, lmargin1=14, lmargin2=14, rmargin=96)

        zoom_row = ctk.CTkFrame(right_frame, fg_color="#FFFFFF")
        zoom_row.pack(fill=tk.X, pady=(6, 2))
        ctk.CTkLabel(zoom_row, text="聊天字号", width=60).pack(side=tk.LEFT)
        ctk.CTkButton(zoom_row, text="A-", width=42, command=lambda: self.adjust_main_chat_font_size(-1)).pack(side=tk.RIGHT)
        ctk.CTkButton(zoom_row, text="A+", width=42, command=lambda: self.adjust_main_chat_font_size(1)).pack(side=tk.RIGHT, padx=(0, 6))

        main_input_row = ctk.CTkFrame(right_frame, fg_color="#FFFFFF")
        main_input_row.pack(fill=tk.X, pady=(8, 10))
        self.main_chat_entry = ctk.CTkEntry(main_input_row, placeholder_text="输入命令：如 结束 chrome.exe")
        self.main_chat_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
        self.main_chat_entry.bind("<Return>", lambda _e: self.on_main_send())
        self.main_send_btn = ctk.CTkButton(main_input_row, text="发送", width=72, command=self.on_main_send)
        self.main_send_btn.pack(side=tk.RIGHT)

    def render_markdown_to_textbox(self):
        """简易 Markdown 清洗渲染：去掉常见 Markdown 控制符并保留可读正文。"""
        self.text_ai.delete("1.0", tk.END)
        current_role = "agent"

        normalized = self.raw_ai_text.replace("\r\n", "\n").replace("\r", "\n")
        lines = normalized.splitlines(keepends=True)

        for raw_line in lines:
            has_newline = raw_line.endswith("\n")
            line = raw_line[:-1] if has_newline else raw_line

            # 归一化常见全角/转义 Markdown 控制符
            line = line.replace("＊", "*").replace("＃", "#")
            line = re.sub(r"\\([#*_`>-])", r"\1", line)

            if re.fullmatch(r"\s*[-*_]{3,}\s*", line) or re.fullmatch(r"\s*[-*]\s*", line):
                continue
            if re.match(r"^\s*```", line):
                continue

            heading_match = re.match(r"^\s*#{1,6}\s*(.+?)\s*$", line)
            if heading_match:
                content = heading_match.group(1)
                content = re.sub(r"^(?:\*\*|__)\s*(.+?)\s*(?:\*\*|__)$", r"\1", content)
                content = content.replace("**", "").replace("__", "")
                content = re.sub(r"^\*+", "", content)
                content = re.sub(r"\*+$", "", content)
                content = content.strip()
                if content:
                    role_key = content.lower()
                    if role_key in ("你", "user"):
                        current_role = "user"
                        self.text_ai.insert(tk.END, content, "main_user_head")
                    elif role_key in ("agent 回复", "agent执行结果", "agent 执行结果", "agent"):
                        current_role = "agent"
                        self.text_ai.insert(tk.END, content, "main_agent_head")
                    else:
                        self.text_ai.insert(tk.END, content, "md_h3")
                if has_newline:
                    self.text_ai.insert(tk.END, "\n")
                continue

            line = re.sub(r"^\s*[-*]\s+", "", line)
            line = re.sub(r"^\s*\d+[\.)]\s+", "", line)
            line = re.sub(r"^\s*>\s?", "", line)
            line = re.sub(r"^\s*#\s+", "", line)

            line = re.sub(r"(^|\s)[#*]+(?=\s|[，。；：,.!?()（）])", r"\1", line)
            line = re.sub(r"(^|\s)-(?=\s|[，。；：,.!?()（）])", r"\1", line)
            line = re.sub(r"^\*+", "", line)
            line = re.sub(r"\*+$", "", line)
            line = re.sub(r"\s{2,}", " ", line)

            start = 0
            body_tag = "main_user" if current_role == "user" else "main_agent"
            bold_tag = "main_user_bold" if current_role == "user" else "main_agent_bold"
            for match in re.finditer(r"(?:\*\*|__)(.+?)(?:\*\*|__)", line):
                plain = line[start:match.start()]
                if plain:
                    self.text_ai.insert(tk.END, plain, body_tag)
                self.text_ai.insert(tk.END, match.group(1), bold_tag)
                start = match.end()

            tail = line[start:]
            tail = tail.replace("**", "").replace("__", "").replace("`", "")
            if tail:
                self.text_ai.insert(tk.END, tail, body_tag)
            if has_newline:
                self.text_ai.insert(tk.END, "\n", body_tag)

        self.text_ai.see(tk.END)

    def data_fetcher_daemon(self):
        """后台守护线程任务：死循环抓取系统信息并推入队列"""
        while True:
            cpu_percent = psutil.cpu_percent(interval=1)
            mem_percent = psutil.virtual_memory().percent
            
            self.data_queue.put({"cpu": cpu_percent, "mem": mem_percent})

            current_time = time.time()
            if current_time - self.last_db_write_time >= 5:
                snapshot = self.latest_top_processes
                if not snapshot.get("cpu") and not snapshot.get("mem"):
                    snapshot = self.collect_top_processes(count=self.top_process_count)
                top_processes_json = json.dumps(snapshot, ensure_ascii=False)
                self.cursor.execute(
                    "INSERT INTO resource_log (timestamp, cpu_percent, mem_percent, top_processes) VALUES (?, ?, ?, ?)",
                    (current_time, cpu_percent, mem_percent, top_processes_json)
                )
                self.conn.commit()
                self.last_db_write_time = current_time

            if current_time - self.last_top_process_time >= self.top_process_interval:
                top_processes = self.collect_top_processes(count=self.top_process_count)
                self.latest_top_processes = top_processes
                self.top_process_queue.put(top_processes)
                self.last_top_process_time = current_time

    def collect_top_processes(self, count=10):
        """同时采集 CPU/内存 双轨 Top 进程快照。"""
        cpu_rows = []
        mem_rows = []
        cpu_cores = max(1, psutil.cpu_count() or 1)

        for proc in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent"]):
            try:
                info = proc.info
                pid = int(info.get("pid") or -1)
                raw_name = (info.get("name") or "").strip()

                # 过滤空闲进程，避免 System Idle Process 干扰 Top 进程榜。
                if pid == 0:
                    continue
                if "idle" in raw_name.lower():
                    continue

                cpu_val = float(info.get("cpu_percent") or 0.0)
                mem_val = float(info.get("memory_percent") or 0.0)
                cpu_val = min(100.0, cpu_val / cpu_cores)

                if not raw_name:
                    raw_name = f"PID {pid if pid >= 0 else '?'}"
                # 控制标签长度，避免遮挡
                name = raw_name if len(raw_name) <= 20 else (raw_name[:17] + "...")

                if cpu_val > 0:
                    cpu_rows.append({"name": name, "value": cpu_val})
                if mem_val > 0:
                    mem_rows.append({"name": name, "value": mem_val})
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
            except Exception:
                continue

        cpu_rows.sort(key=lambda x: x["value"], reverse=True)
        mem_rows.sort(key=lambda x: x["value"], reverse=True)
        return {
            "cpu": cpu_rows[:count],
            "mem": mem_rows[:count],
        }

    def render_top_process_chart(self, proc_data):
        """渲染 Top 进程横向柱状图。"""
        self.ax_proc.clear()
        style_axis_light(self.ax_proc)
        self.ax_proc.grid(False, axis='y')
        self.ax_proc.set_title("Top 进程占用 (%)")
        self.ax_proc.set_xlabel("占用率 (%)")

        payload = normalize_top_processes_payload(proc_data)
        current_track = payload.get("cpu", [])

        if not current_track:
            self.ax_proc.text(0.5, 0.5, "暂无进程数据", ha="center", va="center", color="#666666")
            self.ax_proc.set_xlim(0, 100)
            return

        names = [x["name"] for x in current_track]
        values = [x["value"] for x in current_track]
        # 反转让最大值显示在最上方
        names = names[::-1]
        values = values[::-1]

        bars = self.ax_proc.barh(names, values, color="#ff8c00", edgecolor="#ffb347", alpha=0.95)
        max_val = max(values) if values else 100
        self.ax_proc.set_xlim(0, max(100, max_val * 1.2))

        for bar, val in zip(bars, values):
            self.ax_proc.text(
                bar.get_width() + 1,
                bar.get_y() + bar.get_height() / 2,
                f"{val:.1f}%",
                va="center",
                ha="left",
                color="#ffd7a8",
                fontsize=9,
            )

    def start_worker_thread(self):
        """开启后台数据抓取线程"""
        self.worker_thread = threading.Thread(target=self.data_fetcher_daemon, daemon=True)
        self.worker_thread.start()

    def start_ui_update_loop(self):
        """使用 Tkinter 的 .after() 方法轮询队列以更新图表"""
        try:
            has_new_data = False
            while not self.data_queue.empty():
                data = self.data_queue.get_nowait()
                self.cpu_data.append(data["cpu"])
                self.mem_data.append(data["mem"])
                has_new_data = True
            
            if has_new_data:
                self.line_cpu.set_ydata(self.cpu_data)
                self.line_mem.set_ydata(self.mem_data)
                self.canvas.draw_idle() 
            
        except queue.Empty:
            pass
        finally:
            self.root.after(100, self.start_ui_update_loop)

    def start_top_process_update_loop(self):
        """主线程轮询 Top 进程队列并按较慢节奏刷新柱状图。"""
        try:
            latest = None
            while not self.top_process_queue.empty():
                latest = self.top_process_queue.get_nowait()

            if latest is not None:
                self.render_top_process_chart(latest)
                self.canvas.draw_idle()
        except queue.Empty:
            pass
        finally:
            self.root.after(200, self.start_top_process_update_loop)

    def start_ai_update_loop(self):
        """独立的 AI 宣发轮询队列（不和系统监控冲突）。"""
        try:
            need_render = False
            while not self.ai_queue.empty():
                msg_type, content = self.ai_queue.get_nowait()
                if msg_type == "text":
                    self.raw_ai_text += content
                    need_render = True
                elif msg_type == "status":
                    self.btn_diagnose.configure(state=content)
                elif msg_type == "error_popup":
                    messagebox.showerror("AI 连接失败", content)
                elif msg_type == "main_command":
                    self.handle_main_command(content)

            if need_render:
                self.render_markdown_to_textbox()
        except queue.Empty:
            pass
        finally:
            # 高频轮询以呈现流畅的打字机假象
            self.root.after(50, self.start_ai_update_loop)

    def adjust_main_chat_font_size(self, delta):
        """主界面聊天字体动态缩放，保持自动换行。"""
        new_size = max(self.main_font_min, min(self.main_font_max, self.main_font_size + int(delta)))
        if new_size == self.main_font_size:
            return

        self.main_font_size = new_size
        self.main_user_font.configure(size=self.main_font_size)
        self.main_agent_font.configure(size=self.main_font_size)
        self.md_bold_font.configure(size=self.main_font_size)
        self.md_h3_font.configure(size=self.main_font_size + 4)
        self.main_chat_head_font.configure(size=max(9, self.main_font_size - 1))
        self.text_ai.configure(font=("微软雅黑", self.main_font_size))

        # 触发一次重绘，让历史内容立即应用新字号。
        self.render_markdown_to_textbox()

    def on_main_send(self):
        """主界面 Agent 输入发送。"""
        text = self.main_chat_entry.get().strip()
        if not text:
            return
        self.main_chat_entry.delete(0, tk.END)
        self.ai_queue.put(("text", f"\n### 你\n{text}\n"))
        threading.Thread(target=self.request_main_command_response, args=(text,), daemon=True).start()

    def request_main_command_response(self, user_text):
        """主界面命令协议：chat / kill / draw。"""
        latest_snapshot = normalize_top_processes_payload(self.latest_top_processes)
        context = {
            "current_cpu": float(self.cpu_data[-1]),
            "current_mem": float(self.mem_data[-1]),
            "latest_top_processes": latest_snapshot,
            "top_processes": latest_snapshot,
            "user_text": user_text,
        }

        if DEEPSEEK_API_KEY == "YOUR_API_KEY":
            lowered = user_text.lower()
            wants_draw = any(k in lowered for k in ["画图", "绘图", "可视化", "饼图", "柱状图", "条形图", "pie", "bar"])
            proc_match = re.search(r"([A-Za-z0-9_.-]+\.exe)", user_text, flags=re.IGNORECASE)
            wants_kill = any(k in lowered for k in ["kill", "结束", "关闭", "终止"])
            if wants_draw:
                chart_type = "pie" if any(k in lowered for k in ["饼", "pie"]) else "bar"
                if any(k in lowered for k in ["横", "barh", "水平"]):
                    chart_type = "barh"

                target_data = "top_mem" if any(k in lowered for k in ["内存", "mem", "memory"]) else "top_cpu"
                number_match = re.search(r"(?:top\s*)?(\d{1,2})", lowered)
                top_n = int(number_match.group(1)) if number_match else 3
                top_n = max(1, min(self.top_process_count, top_n))

                self.ai_queue.put(("main_command", {
                    "action": "draw",
                    "process_name": "",
                    "chart_type": chart_type,
                    "target_data": target_data,
                    "top_n": top_n,
                    "reply": "正在为您生成当前进程占比图。",
                }))
                return

            if wants_kill and proc_match:
                self.ai_queue.put(("main_command", {
                    "action": "kill",
                    "process_name": proc_match.group(1),
                    "reply": f"准备执行进程终止：{proc_match.group(1)}",
                }))
            else:
                self.ai_queue.put(("main_command", {
                    "action": "chat",
                    "process_name": "",
                    "reply": "已收到。你可以让我结束某个进程，或让我画 top 进程占比图。",
                }))
            return

        system_prompt = (
            "你是系统运维 Agent。必须返回可直接 json.loads 的纯 JSON，禁止 markdown。"
            "返回格式固定为："
            "{\"action\":\"chat|kill|draw\",\"process_name\":\"string\",\"chart_type\":\"pie|bar|barh\",\"target_data\":\"top_cpu|top_mem\",\"top_n\":3,\"reply\":\"string\"}。"
            "当用户要求结束进程时 action=kill 且 process_name 必填（如 chrome.exe）。"
            "当用户要求画图时 action=draw，必须填写 chart_type、target_data、top_n。"
            "target_data 只能是 top_cpu 或 top_mem。"
        )
        payload = {
            "model": "deepseek-chat",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
            ],
            "stream": False,
            "response_format": {"type": "json_object"},
        }
        headers = {
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json",
        }

        try:
            resp = requests.post(DEEPSEEK_API_URL, headers=headers, json=payload, timeout=20)
            resp.raise_for_status()
            if not resp.text.strip():
                raise ValueError("接口返回为空内容")
            content = resp.json()["choices"][0]["message"]["content"]
            cmd = self.safe_parse_main_command(content)
            self.ai_queue.put(("main_command", cmd))
        except Exception as e:
            self.ai_queue.put(("error_popup", f"主界面 Agent 命令请求失败: {str(e)}"))
            self.ai_queue.put(("main_command", {
                "action": "chat",
                "process_name": "",
                "reply": "抱歉，命令执行链路失败，请检查网络或 API Key。",
            }))

    def safe_parse_main_command(self, raw):
        """主界面指令解析与降级。"""
        text = (raw or "").strip()
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
        try:
            obj = json.loads(text)
        except Exception:
            m = re.search(r"\{[\s\S]*\}", text)
            if m:
                try:
                    obj = json.loads(m.group(0))
                except Exception:
                    obj = {"action": "chat", "process_name": "", "reply": text or "收到"}
            else:
                obj = {"action": "chat", "process_name": "", "reply": text or "收到"}

        chart_type = str(obj.get("chart_type", "bar")).lower().strip()
        if chart_type not in ("pie", "bar", "barh"):
            chart_type = "bar"

        target_data = str(obj.get("target_data", "top_cpu")).lower().strip()
        if target_data not in ("top_cpu", "top_mem"):
            target_data = "top_cpu"

        try:
            top_n = int(obj.get("top_n", 3))
        except (TypeError, ValueError):
            top_n = 3
        top_n = max(1, min(self.top_process_count, top_n))

        return {
            "action": str(obj.get("action", "chat")).lower(),
            "process_name": str(obj.get("process_name", "")).strip(),
            "chart_type": chart_type,
            "target_data": target_data,
            "top_n": top_n,
            "reply": str(obj.get("reply", "收到。")),
        }

    def handle_main_command(self, cmd):
        """执行主界面 Agent 命令（聊天 / 击杀进程 / 动态画图）。"""
        action = str(cmd.get("action", "chat")).lower()
        process_name = str(cmd.get("process_name", "")).strip()
        reply = str(cmd.get("reply", "收到。"))

        if action == "kill":
            if not process_name:
                self.ai_queue.put(("text", "\n### Agent 执行结果\n未提供要结束的进程名。\n"))
                return

            target_process = process_name.strip()
            if not target_process:
                self.ai_queue.put(("text", "\n### Agent 执行结果\n进程名不合法，已拒绝执行。\n"))
                return

            killed_count = 0
            try:
                for proc in psutil.process_iter(['name']):
                    name = proc.info.get('name')
                    if name and name.lower() == target_process.lower():
                        proc.kill()
                        killed_count += 1

                if killed_count > 0:
                    result = f"已成功释放资源，结束了 {killed_count} 个 {target_process} 进程。"
                else:
                    result = f"未找到名为 {target_process} 的活跃进程。"
            except psutil.AccessDenied:
                result = (
                    f"⚠️ 权限受限：{target_process} 是受保护的系统进程或属于其他用户，"
                    "为保护系统稳定，已拒绝击杀。"
                )
            except Exception as e:
                result = f"击杀异常: {str(e)}"

            output = f"\n### Agent 回复\n{reply}\n\n### Agent 执行结果\n{result}\n"
            self.ai_queue.put(("text", output))
        elif action == "draw":
            self.ai_queue.put(("text", f"\n### Agent 回复\n{reply}\n"))
            self.render_main_dynamic_chart(cmd)
        else:
            self.ai_queue.put(("text", f"\n### Agent 回复\n{reply}\n"))

    def render_main_dynamic_chart(self, config):
        """基于最新 Top 进程快照弹出动态图表。"""
        chart_type = str(config.get("chart_type", "bar")).lower().strip()
        if chart_type not in ("pie", "bar", "barh"):
            chart_type = "bar"

        target_data = str(config.get("target_data", "top_cpu")).lower().strip()
        track_key = "mem" if target_data == "top_mem" else "cpu"

        try:
            top_n = int(config.get("top_n", 3))
        except (TypeError, ValueError):
            top_n = 3
        top_n = max(1, min(self.top_process_count, top_n))

        payload = normalize_top_processes_payload(self.latest_top_processes)
        rows = payload.get(track_key, [])[:top_n]

        if not rows:
            messagebox.showinfo("实时可视化分析", "当前暂无可用于绘图的进程快照，请稍后再试。")
            return

        metric_name = "内存" if track_key == "mem" else "CPU"
        chart_name = "饼图" if chart_type == "pie" else ("横向柱状图" if chart_type == "barh" else "柱状图")

        popup = ctk.CTkToplevel(self.root)
        popup.title("实时可视化分析")
        popup.geometry("600x450")
        popup.configure(fg_color="#F9F9F9")
        popup.transient(self.root)
        popup.lift()

        container = ctk.CTkFrame(popup, fg_color="#FFFFFF")
        container.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)

        fig = Figure(figsize=(6, 4), dpi=100)
        fig.patch.set_facecolor("#FFFFFF")
        ax = fig.add_subplot(1, 1, 1)
        style_axis_light(ax)
        ax.grid(False, axis="y")

        names = [str(item.get("name", "未知进程")) for item in rows]
        values = [float(item.get("value") or 0.0) for item in rows]

        if chart_type == "pie":
            total_val = sum(values)
            if total_val <= 0:
                messagebox.showinfo("实时可视化分析", "当前快照占用率均为 0，无法绘制饼图。")
                popup.destroy()
                return

            colors = ["#4A90E2", "#50BFA0", "#F5A623", "#6FA8DC", "#A4C2F4", "#F6B26B", "#93C47D", "#76A5AF", "#C9DAF8", "#FFD966"]
            ax.pie(
                values,
                labels=names,
                autopct=lambda p: f"{p:.1f}%" if p >= 3 else "",
                startangle=90,
                colors=colors[:len(values)],
                textprops={"color": "#333333", "fontsize": 9},
                wedgeprops={"edgecolor": "#FFFFFF", "linewidth": 1.2},
            )
            ax.axis("equal")
            ax.set_title(f"实时 Top{len(rows)} 进程 {metric_name} 占比（{chart_name}）")
        elif chart_type == "barh":
            plot_names = names[::-1]
            plot_values = values[::-1]
            bars = ax.barh(plot_names, plot_values, color="#4A90E2", edgecolor="#9CC3F5", alpha=0.95)
            max_val = max(plot_values) if plot_values else 100
            ax.set_xlim(0, max(100, max_val * 1.2))
            ax.set_xlabel("占用率 (%)")
            ax.set_title(f"实时 Top{len(rows)} 进程 {metric_name} 占比（{chart_name}）")

            for bar, val in zip(bars, plot_values):
                ax.text(
                    bar.get_width() + 1,
                    bar.get_y() + bar.get_height() / 2,
                    f"{val:.1f}%",
                    va="center",
                    ha="left",
                    color="#555555",
                    fontsize=9,
                )
        else:
            bars = ax.bar(names, values, color="#4A90E2", edgecolor="#9CC3F5", alpha=0.95)
            max_val = max(values) if values else 100
            ax.set_ylim(0, max(100, max_val * 1.2))
            ax.set_ylabel("占用率 (%)")
            ax.set_title(f"实时 Top{len(rows)} 进程 {metric_name} 占比（{chart_name}）")
            ax.tick_params(axis="x", rotation=18, labelsize=9)

            for bar, val in zip(bars, values):
                ax.text(
                    bar.get_x() + bar.get_width() / 2,
                    bar.get_height() + 1,
                    f"{val:.1f}%",
                    va="bottom",
                    ha="center",
                    color="#555555",
                    fontsize=9,
                )

        fig.tight_layout()
        popup_canvas = FigureCanvasTkAgg(fig, master=container)
        popup_canvas.draw()
        popup_canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

    def on_history_click(self):
        """弹出智能历史回溯窗口（图表 + 对话式控制台）。"""
        HistoryInsightWindow(self)

    def on_diagnose_click(self):
        """按钮点击响应，生成快照并转移主线程任务给子线程"""
        if self.is_diagnosing: return
        self.is_diagnosing = True
        self.btn_diagnose.configure(state="disabled")
        
        current_cpu = self.cpu_data[-1]
        current_mem = self.mem_data[-1]
        
        # UI 快照分隔符
        self.raw_ai_text += f"\n{'-'*40}\n"
        self.raw_ai_text += f"### 性能快照\n系统当前 CPU: **{current_cpu}%**, 内存占用: **{current_mem}%**\n"
        self.render_markdown_to_textbox()
        
        # 开启全新子线程用于大模型网络请求，绝对不阻塞主 UI 
        threading.Thread(
            target=self._fetch_ai_diagnosis_thread, 
            args=(current_cpu, current_mem), 
            daemon=True
        ).start()

    def _fetch_ai_diagnosis_thread(self, current_cpu, current_mem):
        """子线程网络执行大模型 API"""
        prompt = f"你是一位资深操作系统专家。当前这台电脑的CPU负载为 {current_cpu}%，内存占用为 {current_mem}%。请根据这些硬核数据，给出一份简短、专业的系统性能诊断与优化建议。"
        
        self.ai_queue.put(("text", ">>> 正在连接 AI 推理节点...\n"))
        self.ai_queue.put(("text", ">>> 正在分析系统上下文...\n\n"))

        # NOTE: 在此处替换为你真实的 API_KEY 和 URL
        API_KEY = DEEPSEEK_API_KEY
        API_URL = DEEPSEEK_API_URL

        # 无密钥演示：模拟打字机流式效果
        if API_KEY == "YOUR_API_KEY":
            mock_res = f"【AI 模拟诊断结果】\n观测到当前系统 CPU 在 {current_cpu}% 左右波动，内存占用 {current_mem}%。\n"
            if current_cpu > 80 or current_mem > 80:
                mock_res += "系统处于较高负载状态。若非在进行大型编译或高负荷游戏大作，请立即打开任务管理器检查高占用进程，注意防范内存泄漏或挖矿木马。\n"
            else:
                mock_res += "系统整体资源处于健康分配。后台进程调度平稳，未发现任何显著的卡点，当前电脑非常健康，建议保持！\n"
            
            for char in mock_res:
                self.ai_queue.put(("text", char))
                time.sleep(0.05) # 模拟网络接收数据的延迟，制造打字机效果
                
        else:
            # 真实流式网络请求
            headers = {
                "Authorization": f"Bearer {API_KEY}",
                "Content-Type": "application/json"
            }
            payload = {
                "model": "deepseek-chat",
                "messages": [{"role": "user", "content": prompt}],
                "stream": True # 设为流式传输开启 True
            }
            try:
                response = requests.post(API_URL, headers=headers, json=payload, stream=True, timeout=10)
                response.raise_for_status()
                # 使用 iter_lines 高性能逐行读取分块（SSE 数据格式）
                for line in response.iter_lines(decode_unicode=True):
                    if line and line.startswith("data: "):
                        data_str = line[6:]
                        if data_str == "[DONE]":
                            break
                        try:
                            data_json = json.loads(data_str)
                            chunk = data_json["choices"][0]["delta"].get("content", "")
                            if chunk:
                                self.ai_queue.put(("text", chunk))
                        except json.JSONDecodeError:
                            pass
            except Exception as e:
                self.ai_queue.put((
                    "error_popup",
                    f"大模型接口请求失败，请检查 API Key 或网络连通性。\n详细报错: {str(e)}"
                ))
                self.ai_queue.put(("text", "\n抱歉，由于连接问题，指令无法执行。请修复后重试。\n"))
        
        self.ai_queue.put(("text", "\n\n>>> 诊断结束。\n"))
        self.ai_queue.put(("status", "normal")) # 诊断完成，恢复 AI 报告按钮为可点击
        self.is_diagnosing = False

    def on_close(self):
        try:
            self.conn.close()
        except Exception:
            pass
        self.root.destroy()
