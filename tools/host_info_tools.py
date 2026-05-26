import json
import socket
import subprocess
from pathlib import Path

from config import ALLOWED_WORK_DIR
from tools.command_tools import _truncate_output
from tools.winrm_tools import winrm_command


def _port_open(host: str, port: int, timeout: float = 3.0) -> bool:
    """指定ポートへの TCP 接続が成功するか確認する。"""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def _detect_os(host: str) -> str | None:
    """ポートの応答からOSを推定する。WinRM(5985) → windows / SSH(22) → linux / 不明 → None"""
    if _port_open(host, 5985):
        return "windows"
    if _port_open(host, 22):
        return "linux"
    return None


def _resolve_key_file(key_file: str) -> str | None:
    """key_file をフルパスに解決する。workspace相対パス・ファイル名のみも対応。"""
    p = Path(key_file)
    if p.is_absolute() and p.exists():
        return str(p)
    # workspace 相対
    wp = ALLOWED_WORK_DIR / key_file
    if wp.exists():
        return str(wp)
    # workspace 内をファイル名で検索
    candidates = list(ALLOWED_WORK_DIR.glob(f"**/{p.name}"))
    if candidates:
        return str(candidates[0])
    return None


def _ssh(host: str, username: str, key_path: str, command: str, timeout: int) -> str:
    cmd = [
        "ssh", "-i", key_path,
        "-o", "StrictHostKeyChecking=no",
        "-o", "BatchMode=yes",
        "-o", f"ConnectTimeout={min(timeout, 30)}",
        f"{username}@{host}",
        command,
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip() or r.stderr.strip()
    except subprocess.TimeoutExpired:
        return "ERROR: timeout"
    except Exception as e:
        return f"ERROR: {e}"


def _gather_linux(host: str, username: str, key_path: str, timeout: int) -> dict:
    def run(cmd):
        return _ssh(host, username, key_path, cmd, timeout)

    return {
        "hostname":   run("hostname"),
        "os":         run("cat /etc/os-release 2>/dev/null | grep -E '^(NAME|VERSION|ID)=' | tr -d '\"'"),
        "kernel":     run("uname -r"),
        "cpu":        run("lscpu 2>/dev/null | grep -E 'Architecture|Model name|^CPU\\(s\\):|Thread|Core'"),
        "memory":     run("free -h"),
        "disk":       run("df -h | grep -v tmpfs | grep -v udev"),
        "interfaces": run("ip addr show | grep -E '^[0-9]+:|inet '"),
        "routes":     run("ip route"),
        "dns":        run("grep -v '^#' /etc/resolv.conf 2>/dev/null"),
        "packages":   run(
            "dpkg -l 2>/dev/null | awk 'NR>5 && $1==\"ii\" {print $2, $3}' "
            "|| rpm -qa --queryformat '%{NAME} %{VERSION}\\n' 2>/dev/null"
        ),
        "services":   run(
            "systemctl list-units --type=service --state=running --no-pager --no-legend 2>/dev/null "
            "| awk '{print $1}'"
        ),
        "users":      run("getent passwd | grep -v nologin | grep -v false | cut -d: -f1,3,6"),
        "crontabs":   run("for u in $(cut -d: -f1 /etc/passwd); do crontab -l -u $u 2>/dev/null | grep -v '^#' | sed \"s/^/$u: /\"; done"),
        "open_ports": run("ss -tlnp 2>/dev/null || netstat -tlnp 2>/dev/null"),
    }


_WIN_PS = """\
$e = $ErrorActionPreference; $ErrorActionPreference = 'SilentlyContinue'
$os  = Get-CimInstance Win32_OperatingSystem | Select-Object Caption,Version,OSArchitecture,BuildNumber
$cs  = Get-CimInstance Win32_ComputerSystem  | Select-Object Name,Domain,TotalPhysicalMemory
$cpu = Get-CimInstance Win32_Processor | Select-Object -First 1 Name,NumberOfCores,NumberOfLogicalProcessors,MaxClockSpeed
$out = [ordered]@{
  hostname       = $cs.Name
  domain         = $cs.Domain
  os             = "$($os.Caption) $($os.Version) $($os.OSArchitecture) Build$($os.BuildNumber)"
  cpu            = "$($cpu.Name) Cores:$($cpu.NumberOfCores) Logical:$($cpu.NumberOfLogicalProcessors) $($cpu.MaxClockSpeed)MHz"
  memory_total_gb= [math]::Round($cs.TotalPhysicalMemory/1GB,1)
  disks          = @(Get-PSDrive -PSProvider FileSystem | Select-Object Name,
                      @{N='UsedGB';E={[math]::Round($_.Used/1GB,1)}},
                      @{N='FreeGB';E={[math]::Round($_.Free/1GB,1)}})
  network        = @(Get-NetIPAddress -AddressFamily IPv4 | Where-Object IPAddress -ne '127.0.0.1' |
                      Select-Object InterfaceAlias,IPAddress,PrefixLength)
  dns            = @(Get-DnsClientServerAddress -AddressFamily IPv4 | Where-Object ServerAddresses |
                      Select-Object InterfaceAlias,ServerAddresses)
  default_gw     = (Get-NetRoute -DestinationPrefix '0.0.0.0/0' | Select-Object -First 1 NextHop).NextHop
  packages       = @(Get-ItemProperty 'HKLM:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*',
                      'HKLM:\\SOFTWARE\\WOW6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*' |
                      Where-Object DisplayName | Select-Object DisplayName,DisplayVersion,Publisher |
                      Sort-Object DisplayName)
  services_running = @(Get-Service | Where-Object Status -eq Running |
                        Select-Object Name,DisplayName | Sort-Object Name)
  scheduled_tasks= @(Get-ScheduledTask | Where-Object State -eq Ready |
                      Select-Object TaskName,TaskPath | Sort-Object TaskName)
  local_users    = @(Get-LocalUser | Select-Object Name,Enabled,LastLogon)
  open_ports     = @(Get-NetTCPConnection -State Listen | Select-Object LocalAddress,LocalPort |
                      Sort-Object LocalPort -Unique)
  windows_update = (Get-HotFix | Sort-Object InstalledOn -Descending | Select-Object -First 5 HotFixID,InstalledOn)
}
$ErrorActionPreference = $e
$out | ConvertTo-Json -Depth 4
"""


def _gather_windows(host: str, username: str, password: str, port: int, use_ssl: bool, timeout: int) -> dict:
    result = winrm_command(
        host=host,
        command=_WIN_PS,
        username=username,
        password=password,
        port=port,
        use_ssl=use_ssl,
        timeout_seconds=timeout,
    )
    if result.get("error"):
        return {"error": result["error"]}
    try:
        return json.loads(result["stdout"])
    except Exception:
        return {"raw": _truncate_output(result["stdout"]), "stderr": result.get("stderr", "")}


def gather_host_info(
    host: str,
    os_type: str,
    username: str,
    password: str = None,
    key_file: str = None,
    port: int = None,
    use_ssl: bool = False,
    timeout_seconds: int = 60,
) -> dict:
    """
    Windows / Linux ホストの情報を一括収集して構造化データで返す。

    収集項目:
      共通  : ホスト名・OS・CPU・メモリ・ディスク・NIC・DNS・デフォルトGW・オープンポート
      Windows: インストール済みソフト・実行中サービス・スケジュールタスク・ローカルユーザー・Windows Update履歴
      Linux  : インストール済みパッケージ・実行中サービス・ルーティング・ユーザー・cron・オープンポート

    Args:
        host:            対象ホストの IP またはホスト名
        os_type:         "windows" または "linux"
        username:        ユーザー名
        password:        パスワード（Windows WinRM / Linux パスワード認証時）
        key_file:        SSH 秘密鍵ファイル（workspace 相対パスまたはフルパス）
        port:            ポート番号（省略時: Windows=5985, Linux=22）
        use_ssl:         Windows HTTPS(5986) を使う場合 True
        timeout_seconds: タイムアウト秒数（デフォルト 60）
    """
    os_type = os_type.lower().strip()

    if os_type == "auto":
        detected = _detect_os(host)
        if detected is None:
            return {"error": f"{host} のポート 5985(WinRM) / 22(SSH) どちらにも接続できません。ホストが起動しているか・ファイアウォールを確認してください。"}
        os_type = detected

    if os_type == "windows":
        return {
            "host": host,
            "os_type": "windows",
            **_gather_windows(
                host=host,
                username=username,
                password=password or "",
                port=port or 5985,
                use_ssl=use_ssl,
                timeout=timeout_seconds,
            ),
        }

    elif os_type == "linux":
        key_path = _resolve_key_file(key_file) if key_file else None
        if not key_path:
            return {"error": f"SSH 鍵ファイルが見つかりません: {key_file}"}
        return {
            "host": host,
            "os_type": "linux",
            **_gather_linux(host=host, username=username, key_path=key_path, timeout=timeout_seconds),
        }

    else:
        return {"error": f"os_type は 'windows' または 'linux' を指定してください（指定値: {os_type}）"}
