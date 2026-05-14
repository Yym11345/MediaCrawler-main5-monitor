# LAN Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the current MediaCrawler Monitor easy to run on a Windows LAN host so other devices can open the dashboard through the server IP while the crawler, browser, and PostgreSQL stay on the same machine.

**Architecture:** Keep the backend process bound to `0.0.0.0`, keep all runtime state on the host, and update the monitor UI/help text so users know to visit `http://<server-ip>:8080/monitor` instead of `127.0.0.1`. Add a tiny runtime helper so the bind host and port can be overridden without editing code.

**Tech Stack:** Python, FastAPI, Uvicorn, static HTML, pytest.

---

### Task 1: Add an explicit runtime bind helper

**Files:**
- Modify: `api/main.py`
- Test: `tests/test_api_runtime.py`

- [ ] **Step 1: Write the failing test**

```python
def test_resolve_uvicorn_bind_defaults_to_lan_host(monkeypatch):
    monkeypatch.delenv("API_HOST", raising=False)
    monkeypatch.delenv("API_PORT", raising=False)

    from api.main import resolve_uvicorn_bind

    assert resolve_uvicorn_bind() == ("0.0.0.0", 8080)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_api_runtime.py -v`
Expected: fail because `resolve_uvicorn_bind` does not exist yet.

- [ ] **Step 3: Write minimal implementation**

```python
def resolve_uvicorn_bind() -> tuple[str, int]:
    host = os.getenv("API_HOST", "0.0.0.0").strip() or "0.0.0.0"
    port = int(os.getenv("API_PORT", "8080").strip() or "8080")
    return host, port
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_api_runtime.py -v`
Expected: PASS.

---

### Task 2: Update LAN-facing docs and in-app help

**Files:**
- Modify: `README.md`
- Modify: `api/static/monitor.html`

- [ ] **Step 1: Add a LAN access subsection to the README**

```markdown
## 局域网访问

1. 在服务器上启动服务：

```powershell
uv run python -m uvicorn api.main:app --host 0.0.0.0 --port 8080
```

2. 在服务器上查看局域网 IPv4，例如 `192.168.1.20`。
3. 其他设备访问：`http://192.168.1.20:8080/monitor`
4. Windows 防火墙放行 8080 端口。
```

- [ ] **Step 2: Rewrite help text that says "本机" to mean the hosting machine**

```html
本工具需要运行在直连抖音、小红书、B 站的服务器上。
浏览器登录目录保存在运行服务的机器上 <code>browser_data</code> 下。
```

- [ ] **Step 3: Keep the wording aligned with QR login and SMS verification**

```html
其他设备只负责打开网页查看状态，不需要本地安装浏览器。
```

---

### Task 3: Verify the local deployment guidance

**Files:**
- Test: `tests/test_api_runtime.py`

- [ ] **Step 1: Run the focused tests**

Run: `pytest tests/test_api_runtime.py tests/test_monitor_crawl_limit_ui.py -v`
Expected: PASS.

- [ ] **Step 2: Verify the page still serves**

Run: `python -m uvicorn api.main:app --host 0.0.0.0 --port 8080`
Expected: `/monitor` loads on the host machine and on the LAN IP.

