import tkinter as tk
import tkinter.font as tkfont
import tkinter.messagebox as messagebox
import customtkinter as ctk
import threading
import queue
import time
import datetime
import re
import json
import requests
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from config import DEEPSEEK_API_KEY, DEEPSEEK_API_URL
from utils import style_axis_light, percentile, normalize_top_processes_payload

class HistoryInsightWindow:

    ############初始化
    def __init__(self, app):
        self.app = app
        self.rows = []
        self.history_ai_queue = queue.Queue()
        self.alive = True
        self.streaming = False
        self.current_stream_text = ""
        self.chat_messages = []

        self.window = ctk.CTkToplevel(app.root)
        self.window.title("智能历史回溯与对话式绘图")
        self.window.geometry("980x560")
        self.window.configure(fg_color="#F9F9F9")
        self.window.protocol("WM_DELETE_WINDOW", self.on_close)

        self.setup_ui()
        self.load_history_rows()
        self.render_dynamic_chart({"chart_type": "line", "target_data": "cpu_mem", "top_n": 5})
        self.append_chat("assistant", "已加载历史数据，正在生成初始性能诊断...\n")
        self.start_chat_update_loop()
        threading.Thread(target=self.request_initial_diagnosis, daemon=True).start()

    def setup_ui(self):
        container = ctk.CTkFrame(self.window, fg_color="#F9F9F9")
        container.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)

        self.left_frame = ctk.CTkFrame(container, fg_color="#FFFFFF")
        self.left_frame.place(relx=0, rely=0, relwidth=0.68, relheight=1.0)

        self.right_frame = ctk.CTkFrame(container, fg_color="#FFFFFF")
        self.right_frame.place(relx=0.70, rely=0, relwidth=0.30, relheight=1.0)

        # 左侧图表
        self.hist_fig = Figure(figsize=(6, 4), dpi=100)
        self.hist_fig.patch.set_facecolor("#FFFFFF")
        self.hist_canvas = FigureCanvasTkAgg(self.hist_fig, master=self.left_frame)
        self.hist_canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

        # 右侧对话
        ctk.CTkLabel(self.right_frame, text="AI 历史分析控制台", anchor="w").pack(fill=tk.X, padx=8, pady=(10, 6))
        self.chat_box = ctk.CTkTextbox(self.right_frame, wrap="word", font=("微软雅黑", 11), fg_color="#FFFFFF", text_color="#222222")
        self.chat_box.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))
        self.chat_user_font = tkfont.Font(family="微软雅黑", size=11, weight="bold")
        self.chat_ai_font = tkfont.Font(family="微软雅黑", size=11)
        self.chat_ai_bold_font = tkfont.Font(family="微软雅黑", size=11, weight="bold")
        self.chat_ai_h3_font = tkfont.Font(family="微软雅黑", size=14, weight="bold")
        self.chat_head_font = tkfont.Font(family="微软雅黑", size=10, weight="bold")
        text_widget = self.chat_box._textbox
        text_widget.tag_configure("chat_user_head", foreground="#005FB8", font=self.chat_head_font, lmargin1=96, lmargin2=96, rmargin=14, justify="right", spacing1=8, spacing3=3)
        text_widget.tag_configure("chat_ai_head", foreground="#4A5568", font=self.chat_head_font, lmargin1=14, lmargin2=14, rmargin=96, justify="left", spacing1=8, spacing3=3)
        text_widget.tag_configure("chat_user", foreground="#005FB8", background="#EAF3FF", font=self.chat_user_font, lmargin1=96, lmargin2=96, rmargin=14, justify="right", spacing1=2, spacing3=10)
        text_widget.tag_configure("chat_ai", foreground="#222222", background="#F5F7FA", font=self.chat_ai_font, lmargin1=14, lmargin2=14, rmargin=96, justify="left", spacing1=2, spacing3=10)
        text_widget.tag_configure("chat_ai_bold", foreground="#1F2937", background="#F5F7FA", font=self.chat_ai_bold_font, lmargin1=14, lmargin2=14, rmargin=96)
        text_widget.tag_configure("chat_ai_h3", foreground="#111111", background="#F5F7FA", font=self.chat_ai_h3_font, lmargin1=14, lmargin2=14, rmargin=96, spacing1=4, spacing3=6)
        text_widget.tag_configure("chat_stream", foreground="#444444", background="#F5F7FA", font=self.chat_ai_font, lmargin1=14, lmargin2=14, rmargin=96, justify="left", spacing1=2, spacing3=10)

        input_row = ctk.CTkFrame(self.right_frame, fg_color="#FFFFFF")
        input_row.pack(fill=tk.X, padx=8, pady=(0, 10))
        self.chat_entry = ctk.CTkEntry(input_row, placeholder_text="输入：比如“帮我画个雷达图”")
        self.chat_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
        self.chat_entry.bind("<Return>", lambda _e: self.on_send())
        self.send_btn = ctk.CTkButton(input_row, text="发送", width=72, command=self.on_send)
        self.send_btn.pack(side=tk.RIGHT)

        self.clear_btn = ctk.CTkButton(
            self.right_frame,
            text="🗑️ 清空历史数据",
            fg_color="#C0392B",
            hover_color="#A93226",
            command=self.clear_history_data,
        )
        self.clear_btn.pack(fill=tk.X, padx=8, pady=(0, 10))

    ################读数据
    def load_history_rows(self):
        cur = self.app.conn.cursor()
        cur.execute("SELECT timestamp, cpu_percent, mem_percent, top_processes FROM resource_log ORDER BY id DESC LIMIT 300")
        self.rows = cur.fetchall()
        self.rows.reverse()

    def build_summary(self):
        if not self.rows:
            return {
                "count": 0,
                "message": "暂无历史数据"
            }

        cpu_vals = [float(r[1]) for r in self.rows]
        mem_vals = [float(r[2]) for r in self.rows]
        start_ts = datetime.datetime.fromtimestamp(self.rows[0][0]).strftime('%Y-%m-%d %H:%M:%S')
        end_ts = datetime.datetime.fromtimestamp(self.rows[-1][0]).strftime('%Y-%m-%d %H:%M:%S')

        summary = {
            "count": len(self.rows),
            "range": {"start": start_ts, "end": end_ts},
            "cpu": {
                "avg": round(sum(cpu_vals) / len(cpu_vals), 2),
                "max": round(max(cpu_vals), 2),
                "min": round(min(cpu_vals), 2),
                "p95": round(percentile(cpu_vals, 0.95), 2),
            },
            "mem": {
                "avg": round(sum(mem_vals) / len(mem_vals), 2),
                "max": round(max(mem_vals), 2),
                "min": round(min(mem_vals), 2),
                "p95": round(percentile(mem_vals, 0.95), 2),
            },
        }
        return summary

    def append_chat(self, role, text):
        self.chat_messages.append({"role": role, "text": text})
        self.render_chat_messages()

    #####清洗ai的#---等字符
    def render_ai_markdown_segment(self, text):
        """AI Markdown 清洗渲染：去掉标题/分隔符/粗体等标记符号。"""
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        lines = normalized.splitlines(keepends=True)

        for raw_line in lines:
            has_newline = raw_line.endswith("\n")
            line = raw_line[:-1] if has_newline else raw_line

            # 归一化常见全角/转义 Markdown 控制符
            line = line.replace("＊", "*").replace("＃", "#")
            line = re.sub(r"\\([#*_`>-])", r"\1", line)

            # 跳过 Markdown 分隔线和孤立列表符号
            if re.fullmatch(r"\s*[-*_]{3,}\s*", line) or re.fullmatch(r"\s*[-*]\s*", line):
                continue
            if re.match(r"^\s*```", line):
                continue

            heading_match = re.match(r"^\s*#{1,6}\s*(.+?)\s*$", line)
            if heading_match:
                title = heading_match.group(1)
                title = re.sub(r"^(?:\*\*|__)\s*(.+?)\s*(?:\*\*|__)$", r"\1", title)
                title = title.replace("**", "").replace("__", "")
                title = re.sub(r"^\*+", "", title)
                title = re.sub(r"\*+$", "", title)
                title = title.strip()
                if title:
                    self.chat_box.insert(tk.END, title, "chat_ai_h3")
                if has_newline:
                    self.chat_box.insert(tk.END, "\n", "chat_ai_h3")
                continue

            # 去掉列表前缀符号（如 "- 文本"）
            line = re.sub(r"^\s*[-*]\s+", "", line)
            line = re.sub(r"^\s*\d+[\.)]\s+", "", line)
            line = re.sub(r"^\s*>\s?", "", line)
            line = re.sub(r"^\s*#\s+", "", line)

            # 去掉行内孤立的 Markdown 控制符（避免残留 * - #）
            line = re.sub(r"(^|\s)[#*]+(?=\s|[，。；：,.!?()（）])", r"\1", line)
            line = re.sub(r"(^|\s)-(?=\s|[，。；：,.!?()（）])", r"\1", line)
            line = re.sub(r"^\*+", "", line)
            line = re.sub(r"\*+$", "", line)
            line = re.sub(r"\s{2,}", " ", line)

            start = 0
            for match in re.finditer(r"(?:\*\*|__)(.+?)(?:\*\*|__)", line):
                plain = line[start:match.start()]
                if plain:
                    self.chat_box.insert(tk.END, plain, "chat_ai")
                self.chat_box.insert(tk.END, match.group(1), "chat_ai_bold")
                start = match.end()

            tail = line[start:]
            tail = tail.replace("**", "").replace("__", "").replace("`", "")
            if tail:
                self.chat_box.insert(tk.END, tail, "chat_ai")
            if has_newline:
                self.chat_box.insert(tk.END, "\n", "chat_ai")

    def render_chat_messages(self, include_stream=False):
        self.chat_box.delete("1.0", tk.END)

        for msg in self.chat_messages:
            if msg["role"] == "user":
                self.chat_box.insert(tk.END, "你\n", "chat_user_head")
                self.chat_box.insert(tk.END, msg["text"].rstrip("\n") + "\n\n", "chat_user")
            else:
                self.chat_box.insert(tk.END, "AI\n", "chat_ai_head")
                self.render_ai_markdown_segment(msg["text"])
                if not msg["text"].endswith("\n"):
                    self.chat_box.insert(tk.END, "\n", "chat_ai")
                self.chat_box.insert(tk.END, "\n", "chat_ai")

        if include_stream and self.current_stream_text:
            self.chat_box.insert(tk.END, "AI\n", "chat_ai_head")
            self.render_ai_markdown_segment(self.current_stream_text)
            if not self.current_stream_text.endswith("\n"):
                self.chat_box.insert(tk.END, "\n", "chat_ai")

        self.chat_box.see(tk.END)

    def start_stream(self):
        self.streaming = True
        self.current_stream_text = ""

    def stream_token(self, token):
        self.current_stream_text += token
        self.render_chat_messages(include_stream=True)

    def end_stream(self):
        if self.streaming and self.current_stream_text:
            # 流式结束后执行一次全量 Markdown 清洗和重绘
            self.chat_messages.append({"role": "assistant", "text": self.current_stream_text})
            self.current_stream_text = ""
            self.render_chat_messages()
        self.streaming = False

    def request_initial_diagnosis(self):
        summary = self.build_summary()
        if summary.get("count", 0) == 0:
            self.history_ai_queue.put(("assistant", "暂无可分析历史数据。"))
            return

        prompt = (
            "你是资深系统性能分析专家。请根据以下历史数据摘要输出简洁诊断，"
            "重点说明负载波动与优化建议。\n"
            f"摘要JSON: {json.dumps(summary, ensure_ascii=False)}"
        )
        try:
            self.stream_plain_response(prompt)
        except Exception as e:
            self.history_ai_queue.put((
                "error_popup",
                f"大模型接口请求失败，请检查 API Key 或网络连通性。\n详细报错: {str(e)}"
            ))
            self.history_ai_queue.put(("assistant", "抱歉，由于连接问题，指令无法执行。请修复后重试。"))

    def on_send(self):
        text = self.chat_entry.get().strip()
        if not text:
            return
        self.chat_entry.delete(0, tk.END)
        self.append_chat("user", text)
        threading.Thread(target=self.request_command_response, args=(text,), daemon=True).start()

    def request_command_response(self, user_text):
        summary = self.build_summary()
        
        # 【新增灵魂逻辑】提前查出当前的 Top 3 进程，喂给大模型作为上下文
        proc_names, proc_values = self.query_top_process_averages(3)
        summary["top_processes_hint"] = [f"{n} ({v:.1f}%)" for n, v in zip(proc_names, proc_values)]

        system_prompt = (
            "你是AIOps数据分析与可视化调度员。"
            "你必须输出可被 json.loads() 直接解析的纯 JSON，不要 markdown 代码块。"
            "严格使用以下结构："
            "{\"action\":\"draw|chat\",\"chart_type\":\"bar|pie|line\","
            "\"target_data\":\"cpu_mem|top_processes|specific_process\",\"process_name\":\"string\",\"top_n\":5,\"reply\":\"string\"}。"
            "规则1：如果用户要求画图，action=draw，设置对应的参数，并在 reply 中回复确认。"
            "规则2：如果用户提问具体数据（如占用最高的进程是谁），action=chat，你必须阅读我发给你的 history_summary 里的 top_processes_hint 和其他数据，在 reply 中给出准确的自然语言回答！"
            "规则3：如果用户要求追踪某个具体进程趋势，target_data=specific_process 且 process_name 必填。"
        )
        user_payload = {
            "user_request": user_text,
            "history_summary": summary,
            "available_chart_types": ["line", "bar", "pie"],
            "available_target_data": ["cpu_mem", "top_processes", "specific_process"],
        }

        if DEEPSEEK_API_KEY == "YOUR_API_KEY":
            lower = user_text.lower()
            top_n = 5
            m = re.search(r"(\d+)", user_text)
            if m:
                try:
                    top_n = max(1, min(10, int(m.group(1))))
                except ValueError:
                    top_n = 5

            target = "top_processes" if ("进程" in user_text or "process" in lower) else "cpu_mem"
            process_name = ""
            m_proc = re.search(r"([A-Za-z0-9_.-]+\.exe)", user_text, flags=re.IGNORECASE)
            if m_proc:
                process_name = m_proc.group(1)

            if "追踪" in user_text or "特定" in user_text:
                target = "specific_process"

            if "饼" in user_text or "pie" in lower:
                action = {
                    "action": "draw",
                    "chart_type": "pie",
                    "target_data": target,
                    "process_name": process_name,
                    "top_n": top_n,
                    "reply": "好的，马上为你绘制饼图。",
                }
            elif "柱" in user_text or "bar" in lower:
                action = {
                    "action": "draw",
                    "chart_type": "bar",
                    "target_data": target,
                    "process_name": process_name,
                    "top_n": top_n,
                    "reply": "好的，马上为你绘制柱状图。",
                }
            elif "线" in user_text or "line" in lower or "趋势" in user_text:
                action = {
                    "action": "draw",
                    "chart_type": "line",
                    "target_data": target,
                    "process_name": process_name,
                    "top_n": top_n,
                    "reply": "好的，马上为你绘制历史趋势折线图。",
                }
            else:
                action = {
                    "action": "chat",
                    "chart_type": "line",
                    "target_data": "cpu_mem",
                    "process_name": process_name,
                    "top_n": top_n,
                    "reply": "我可以帮你画历史折线图、进程柱状图或进程饼图。",
                }
            self.history_ai_queue.put(("command", action))
            return

        headers = {
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": "deepseek-chat",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
            ],
            "stream": False,
            "response_format": {"type": "json_object"},
        }

        try:
            resp = requests.post(DEEPSEEK_API_URL, headers=headers, json=payload, timeout=20)
            resp.raise_for_status()
            if not resp.text.strip():
                raise ValueError("接口返回为空内容")
            content = resp.json()["choices"][0]["message"]["content"]
            parsed = self.safe_parse_command(content)
            self.history_ai_queue.put(("command", parsed))
        except Exception as e:
            self.history_ai_queue.put((
                "error_popup",
                f"大模型接口请求失败，请检查 API Key 或网络连通性。\n详细报错: {str(e)}"
            ))
            self.history_ai_queue.put(("assistant", "抱歉，由于连接问题，指令无法执行。请修复后重试。"))

    def safe_parse_command(self, raw):
        text = (raw or "").strip()
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)

        try:
            parsed = json.loads(text)
        except Exception:
            m = re.search(r"\{[\s\S]*\}", text)
            if m:
                try:
                    parsed = json.loads(m.group(0))
                except Exception:
                    return {
                        "action": "chat",
                        "chart_type": "line",
                        "target_data": "cpu_mem",
                        "top_n": 5,
                        "reply": text or "我暂时无法稳定解析绘图指令，已保留为普通回复。",
                    }
            else:
                return {
                    "action": "chat",
                    "chart_type": "line",
                    "target_data": "cpu_mem",
                    "top_n": 5,
                    "reply": text or "我暂时无法稳定解析绘图指令，已保留为普通回复。",
                }

        return {
            "action": str(parsed.get("action", "chat")).lower(),
            "chart_type": str(parsed.get("chart_type", "line")).lower(),
            "target_data": str(parsed.get("target_data", "cpu_mem")).lower(),
            "process_name": str(parsed.get("process_name", "")).strip(),
            "top_n": int(parsed.get("top_n", 5) or 5),
            "reply": str(parsed.get("reply", "收到。")),
        }

    def stream_plain_response(self, prompt):
        if DEEPSEEK_API_KEY == "YOUR_API_KEY":
            mock = "根据最近历史负载，系统整体处于中等波动区间。建议关注短时CPU尖峰与内存上沿，并优化后台高频任务。"
            self.history_ai_queue.put(("stream_start", None))
            for ch in mock:
                self.history_ai_queue.put(("stream_token", ch))
                time.sleep(0.02)
            self.history_ai_queue.put(("stream_end", None))
            return

        headers = {
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": "deepseek-chat",
            "messages": [{"role": "user", "content": prompt}],
            "stream": True,
        }

        self.history_ai_queue.put(("stream_start", None))
        try:
            response = requests.post(DEEPSEEK_API_URL, headers=headers, json=payload, stream=True, timeout=20)
            response.raise_for_status()
            for line in response.iter_lines(decode_unicode=True):
                if line and line.startswith("data: "):
                    body = line[6:]
                    if body == "[DONE]":
                        break
                    try:
                        chunk = json.loads(body)["choices"][0]["delta"].get("content", "")
                        if chunk:
                            self.history_ai_queue.put(("stream_token", chunk))
                    except Exception:
                        continue
        except Exception as e:
            self.history_ai_queue.put((
                "error_popup",
                f"大模型接口请求失败，请检查 API Key 或网络连通性。\n详细报错: {str(e)}"
            ))
            self.history_ai_queue.put(("assistant", "抱歉，由于连接问题，指令无法执行。请修复后重试。"))
        finally:
            self.history_ai_queue.put(("stream_end", None))

    def start_chat_update_loop(self):
        if not self.alive:
            return
        try:
            while not self.history_ai_queue.empty():
                msg_type, payload = self.history_ai_queue.get_nowait()
                if msg_type == "stream_start":
                    self.start_stream()
                elif msg_type == "stream_token":
                    self.stream_token(payload)
                elif msg_type == "stream_end":
                    self.end_stream()
                elif msg_type == "assistant":
                    self.append_chat("assistant", payload)
                elif msg_type == "error_popup":
                    messagebox.showerror("AI 连接失败", payload)
                elif msg_type == "command":
                    self.handle_command(payload)
        except queue.Empty:
            pass
        finally:
            self.window.after(80, self.start_chat_update_loop)

    def handle_command(self, cmd):
        action = str(cmd.get("action", "chat")).lower()
        reply = str(cmd.get("reply", "收到。"))

        if action == "draw":
            config = {
                "chart_type": str(cmd.get("chart_type", "line")).lower(),
                "target_data": str(cmd.get("target_data", "cpu_mem")).lower(),
                "process_name": str(cmd.get("process_name", "")).strip(),
                "top_n": int(cmd.get("top_n", 5) or 5),
            }
            self.append_chat("assistant", reply)
            self.render_dynamic_chart(config)
        elif action == "chat":
            self.append_chat("assistant", reply)
        else:
            # 解析失败或非法动作时降级为纯聊天
            self.append_chat("assistant", reply)

    def render_dynamic_chart(self, config):
        """统一动态渲染入口：按配置路由数据查询与图表绘制。"""
        chart_type = str(config.get("chart_type", "line")).lower()
        target_data = str(config.get("target_data", "cpu_mem")).lower()
        try:
            top_n = int(config.get("top_n", 5) or 5)
        except Exception:
            top_n = 5
        top_n = max(1, min(10, top_n))
        process_name = str(config.get("process_name", "")).strip()

        if target_data not in ("cpu_mem", "top_processes", "specific_process"):
            target_data = "cpu_mem"
        if chart_type not in ("line", "bar", "pie"):
            chart_type = "line"

        self.hist_fig.clf()
        ax = self.hist_fig.add_subplot(1, 1, 1)

        if target_data == "cpu_mem":
            self.draw_cpu_mem_line(ax)
        elif target_data == "specific_process":
            if not process_name:
                names, _ = self.query_top_process_averages(1)
                process_name = names[0] if names else ""
            self.draw_specific_process_line(ax, process_name)
        else:
            proc_names, proc_values = self.query_top_process_averages(top_n)
            if not proc_names:
                style_axis_light(ax)
                ax.set_title("Top进程历史分析")
                ax.text(0.5, 0.5, "暂无可用进程历史数据", ha="center", va="center", color="#d9d9d9")
            elif chart_type == "pie":
                self.draw_top_process_pie(ax, proc_names, proc_values)
            else:
                self.draw_top_process_bar(ax, proc_names, proc_values)

        self.hist_canvas.draw_idle()

    def draw_cpu_mem_line(self, ax):
        style_axis_light(ax)
        ax.set_title("历史系统负载趋势")
        ax.set_xlabel("时间")
        ax.set_ylabel("使用率 (%)")

        if not self.rows:
            ax.text(0.5, 0.5, "暂无历史数据", ha="center", va="center")
            return

        times = [datetime.datetime.fromtimestamp(r[0]).strftime('%H:%M:%S') for r in self.rows]
        cpus = [r[1] for r in self.rows]
        mems = [r[2] for r in self.rows]
        ax.plot(times, cpus, label="CPU (%)", color="#005FB8", linewidth=1.8)
        ax.plot(times, mems, label="内存 (%)", color="#107C10", linewidth=1.8)
        ax.legend()
        if len(times) > 10:
            step = max(1, len(times) // 10)
            ticks = list(range(0, len(times), step))
            ax.set_xticks(ticks)
            ax.set_xticklabels([times[i] for i in ticks], rotation=30, ha='right')

    def query_top_process_averages(self, top_n):
        cur = self.app.conn.cursor()
        cur.execute(
            "SELECT top_processes FROM resource_log WHERE top_processes IS NOT NULL AND top_processes != '' ORDER BY id DESC LIMIT 1000"
        )
        rows = cur.fetchall()

        stats = {}
        for row in rows:
            raw = row[0]
            if not raw:
                continue
            payload = normalize_top_processes_payload(raw)
            cpu_list = payload.get("cpu", [])

            for item in cpu_list:
                name = str(item.get("name", "")).strip()
                if not name:
                    continue
                try:
                    val = float(item.get("value", 0.0) or 0.0)
                except Exception:
                    continue
                if val <= 0:
                    continue

                if name not in stats:
                    stats[name] = {"sum": 0.0, "count": 0}
                stats[name]["sum"] += val
                stats[name]["count"] += 1

        if not stats:
            return [], []

        ranked = []
        for name, meta in stats.items():
            avg = meta["sum"] / max(1, meta["count"])
            ranked.append((name, avg))

        ranked.sort(key=lambda x: x[1], reverse=True)
        top = ranked[:top_n]
        names = [x[0] if len(x[0]) <= 20 else (x[0][:17] + "...") for x in top]
        values = [x[1] for x in top]
        return names, values

    def draw_specific_process_line(self, ax, process_name):
        """绘制指定进程随时间变化的 CPU/内存占用双折线。"""
        style_axis_light(ax)
        title_name = process_name if process_name else "(未指定)"
        ax.set_title(f"进程追踪: {title_name}")
        ax.set_xlabel("时间")
        ax.set_ylabel("占用率 (%)")

        if not self.rows:
            ax.text(0.5, 0.5, "暂无历史数据", ha="center", va="center")
            return

        target = process_name.lower().strip()
        times = []
        cpu_vals = []
        mem_vals = []

        for row in self.rows:
            ts, _cpu, _mem, raw_top = row
            times.append(datetime.datetime.fromtimestamp(ts).strftime('%H:%M:%S'))

            payload = normalize_top_processes_payload(raw_top)
            cpu_items = payload.get("cpu", [])
            mem_items = payload.get("mem", [])

            cval = 0.0
            mval = 0.0

            for item in cpu_items:
                name = str(item.get("name", "")).lower().strip()
                if name == target:
                    try:
                        cval = float(item.get("value", 0.0) or 0.0)
                    except Exception:
                        cval = 0.0
                    break

            for item in mem_items:
                name = str(item.get("name", "")).lower().strip()
                if name == target:
                    try:
                        mval = float(item.get("value", 0.0) or 0.0)
                    except Exception:
                        mval = 0.0
                    break

            cpu_vals.append(cval)
            mem_vals.append(mval)

        ax.plot(times, cpu_vals, label="CPU (%)", color="#005FB8", linewidth=1.8)
        ax.plot(times, mem_vals, label="内存 (%)", color="#107C10", linewidth=1.8)
        ax.legend()
        if len(times) > 10:
            step = max(1, len(times) // 10)
            ticks = list(range(0, len(times), step))
            ax.set_xticks(ticks)
            ax.set_xticklabels([times[i] for i in ticks], rotation=30, ha='right')

    def clear_history_data(self):
        """清空历史数据并刷新界面。"""
        try:
            self.app.cursor.execute("DELETE FROM resource_log")
            self.app.conn.commit()
            self.rows = []
            self.render_dynamic_chart({"chart_type": "line", "target_data": "cpu_mem", "top_n": 5})
            self.append_chat("assistant", "历史数据已清空，开始重新记录。")
        except Exception as e:
            messagebox.showerror("清空失败", f"历史数据清空失败: {str(e)}")

    def draw_top_process_bar(self, ax, names, values):
        style_axis_light(ax)
        ax.grid(False, axis='y')
        ax.set_title("Top进程平均占用（历史）")
        ax.set_xlabel("平均占用率 (%)")
        show_names = names[::-1]
        show_vals = values[::-1]
        bars = ax.barh(show_names, show_vals, color="#ff8c00", edgecolor="#ffb347", alpha=0.95)
        vmax = max(show_vals) if show_vals else 100
        ax.set_xlim(0, max(100, vmax * 1.2))
        for bar, val in zip(bars, show_vals):
            ax.text(bar.get_width() + 1, bar.get_y() + bar.get_height()/2, f"{val:.1f}%", va="center", ha="left", color="#ffd7a8", fontsize=9)

    def draw_top_process_pie(self, ax, names, values):
        ax.set_facecolor("#FFFFFF")
        ax.set_title("Top进程平均占用占比（历史）", color="#333333")
        colors = ["#ff8c00", "#b56dff", "#3fbf7f", "#2ea8ff", "#f25f5c", "#ffd166"]
        total = sum(values)
        if total <= 0:
            ax.text(0.5, 0.5, "暂无可用进程历史数据", ha="center", va="center", color="#d9d9d9")
            return
        ax.pie(
            values,
            labels=names,
            colors=colors[:len(values)],
            autopct="%1.1f%%",
            textprops={"color": "#333333"},
            startangle=90,
        )

    def on_close(self):
        self.alive = False
        self.window.destroy()

