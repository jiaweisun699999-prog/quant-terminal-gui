# Quant-Terminal

本地运行的辅助定投决策工具：**能联网则用 AkShare 拉数据**，失败则通过对话框或 CLI **手工补齐**，策略计算与 SQLite 账本不中断。支持 **命令行**、**Textual 终端 UI**、**Qt 桌面 GUI**，并可 **Windows 一键打包为 exe**。

## 环境要求

| 项 | 说明 |
|----|------|
| Python | **3.10+**（开发验证过 3.12） |
| 系统 | 优先 **Windows**；CLI 逻辑跨平台，Qt 打包脚本面向 Windows |
| 网络 | 拉取行情时需能访问 AkShare 所用数据源；弱网可在 Qt **设置** 中调大 **ERP 阶段软超时** |

---

## 快速部署（推荐给协作者）

### 1. 获取代码

```powershell
git clone <你的仓库 URL>
cd <仓库根目录>/Quant-Terminal
```

### 2. 创建虚拟环境并安装依赖

**方式 A：在 `Quant-Terminal` 目录下建 venv（推荐）**

```powershell
cd Quant-Terminal
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

**方式 B：在仓库根目录建 venv**

将 `.\.venv\Scripts\python.exe` 传给打包脚本即可；运行前仍需 `Activate` 或在 `Quant-Terminal` 下用该解释器执行 `python qt_app.py`。

### 3. 运行方式（任选其一）

**Qt 桌面界面（推荐）**

```powershell
# 已在 Quant-Terminal 目录且已 Activate
python qt_app.py
```

**命令行战报**

```powershell
python main.py --date 2026-04-17 --take-profit 0
```

**Textual 终端 UI**

```powershell
python ui_app.py
```

首次运行会在当前工作目录生成 **`quant_terminal.db`**（SQLite）。数据库文件名可在 `config.py` / 设置中调整。

---

## Windows 打包为 exe

在 **`Quant-Terminal`** 目录执行（会自动 `pip install -r requirements.txt` 再调用 PyInstaller）：

```powershell
cd Quant-Terminal
.\build_exe.ps1
```

- **输出**：`.\dist\Quant-Terminal.exe`
- **指定 Python**：`.\build_exe.ps1 -PythonExe "C:\path\to\python.exe"`
- **脚本未写 `-PythonExe` 时**：依次尝试 `Quant-Terminal\.venv\Scripts\python.exe` → 上一级 `..\.venv\Scripts\python.exe` → 环境变量中的 `python`

打包参数已包含对 **AkShare 数据文件**（如 `file_fold/calendar.json`）与 **py_mini_racer**（`mini_racer.dll`、`icudtl.dat`）的收集，避免 onefile 下缺文件或缺原生库。

也可使用 **`Quant-Terminal.spec`**：

```powershell
pyinstaller Quant-Terminal.spec
```

---

## 常见问题（exe / 多机部署）

### 1. 手工回退弹窗或 ERP 超时

- 弱网、重试多、接口慢时，整段 ERP 可能超过默认软超时。打开 Qt **设置**，调大 **「ERP 阶段软超时(秒)」**（例如 **90～120**），保存后再运行或重新打包（若把默认值写进 `config.py` 亦可）。
- 软超时基于线程池**墙钟**等待，超时后界面会提示兜底，后台线程仍可能继续跑完并在日志里打出后续行，属已知现象。

### 2. `calendar.json` / AkShare 数据找不到

确保使用当前仓库的 **`build_exe.ps1` 或 `Quant-Terminal.spec`**（含 `--collect-data akshare`），并重新打包。

### 3. `mini_racer.dll` / `py_mini_racer` 报错

`stock_index_pe_lg` 等接口依赖 **py_mini_racer** 原生文件。请使用含 **`--collect-data py_mini_racer`** 的打包脚本重新构建。

### 4. HTTPS / 证书错误（exe）

程序在 frozen 模式下会尝试设置 `SSL_CERT_FILE`（certifi）。若仍失败，检查本机代理、防火墙或公司 MITM 证书。

### 5. 无控制台 exe 下 tqdm / 持仓分析报错

`--noconsole` 下 `stdout`/`stderr` 可能为 `None`。`qt_app.py` 已对 frozen 环境做占位与 `TQDM_DISABLE`；请使用**最新代码**重新打包。

### 6. 本机正常、另一台电脑异常

多为 **网络延迟、不稳定（IncompleteRead）** 或 **超时过短**，参见第 1 条。

---

## 项目结构（核心文件）

| 文件 | 作用 |
|------|------|
| `config.py` | 参数、阈值、`fetch_retries`、`erp_build_timeout_s` 等 |
| `main.py` | CLI 入口、`build_weekly_plan`、战报 |
| `qt_app.py` | Qt 主程序、frozen 运行时配置 |
| `qt_worker.py` | 后台运行、超时与日志 |
| `data_fetcher.py` | AkShare 抓取与重试、可选 trace |
| `portfolio_analysis.py` | 持仓与行情分析 |
| `storage.py` | SQLite 账本 |
| `strategy.py` | 纯策略计算 |
| `build_exe.ps1` / `Quant-Terminal.spec` | Windows 打包 |

---

## 功能与规则摘要

- **ERP**：\(ERP = (1/PE)\times100 - \) 十年期国债收益率（%）等逻辑见策略模块。
- **滴灌**：止盈利润进入队列，按周释放；**仅在周三且同一自然周释放一次**（详见代码与注释）。

---

## Git 工作流建议

```powershell
git checkout -b <你的分支名>
# 修改、测试后
git add .
git commit -m "描述本次改动的完整句子"
git push -u origin <你的分支名>
```

若远程尚未配置：`git remote add origin <URL>` 后再 `git push`。
