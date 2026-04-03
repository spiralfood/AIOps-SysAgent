import json


##########修改背景颜色############################
def style_axis_light(ax):
    """统一浅色图表配色，保证与 CTk 浅色主题一致。"""
    ax.set_facecolor("#FFFFFF")
    for spine in ax.spines.values():
        spine.set_color("#E0E0E0")
    ax.tick_params(colors="#333333")
    ax.xaxis.label.set_color("#333333")
    ax.yaxis.label.set_color("#333333")
    ax.title.set_color("#333333")
    ax.grid(True, linestyle='--', alpha=1.0, color="#F0F0F0")


def percentile(values, p):
    """无第三方依赖的百分位数计算。"""
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    idx = int((len(sorted_vals) - 1) * p)
    return float(sorted_vals[max(0, min(len(sorted_vals) - 1, idx))])


###########对读取到的进程进行过滤，清晰
def _normalize_top_process_items(items):
    """标准化进程条目列表，确保 name/value 可用。"""
    normalized = []
    if not isinstance(items, list):
        return normalized

    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        try:
            val = float(item.get("value", 0.0) or 0.0)
        except Exception:
            continue
        if val < 0:
            continue
        normalized.append({"name": name, "value": val})
    return normalized


###############兼容新格式和旧格式################
def normalize_top_processes_payload(raw):
    """兼容旧格式(list)与新格式(dict{cpu,mem})，统一返回双轨结构。"""
    data = raw
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except Exception:
            return {"cpu": [], "mem": []}

    # 旧格式：直接是列表，默认视为 cpu 轨迹
    if isinstance(data, list):
        return {"cpu": _normalize_top_process_items(data), "mem": []}

    if isinstance(data, dict):
        cpu_items = _normalize_top_process_items(data.get("cpu", []))
        mem_items = _normalize_top_process_items(data.get("mem", []))
        return {"cpu": cpu_items, "mem": mem_items}

    return {"cpu": [], "mem": []}

