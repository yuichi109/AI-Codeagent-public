import winrm
from tools.command_tools import _truncate_output

WINRM_TIMEOUT = 30


def winrm_command(
    host: str,
    command: str,
    username: str,
    password: str,
    port: int = 5985,
    use_ssl: bool = False,
    transport: str = "ntlm",
    timeout_seconds: int = WINRM_TIMEOUT,
) -> dict:
    """
    WinRM 経由でリモート Windows に PowerShell コマンドを実行する。

    TrustedHosts 設定不要・ドメイン未参加環境でも動作する。
    コマンドは Base64 エンコードで渡すためパイプ・スクリプトブロック等も正しく動作する。

    Args:
        host:            接続先 IP またはホスト名
        command:         実行する PowerShell コマンド（パイプ・スクリプトブロック使用可）
        username:        ユーザー名（例: Administrator, DOMAIN\\user）
        password:        パスワード
        port:            ポート番号（HTTP=5985, HTTPS=5986）
        use_ssl:         True で HTTPS(5986) を使用（証明書検証はスキップ）
        transport:       認証方式 ntlm / kerberos / basic / credssp
        timeout_seconds: タイムアウト秒数
    """
    try:
        session = winrm.Session(
            target=f"{'https' if use_ssl else 'http'}://{host}:{port}/wsman",
            auth=(username, password),
            transport=transport,
            server_cert_validation="ignore",
            operation_timeout_sec=timeout_seconds,
            read_timeout_sec=timeout_seconds + 10,
        )
        result = session.run_ps(command)
        return {
            "stdout": _truncate_output(result.std_out.decode("utf-8", errors="replace")),
            "stderr": _truncate_output(result.std_err.decode("utf-8", errors="replace"), 4000),
            "returncode": result.status_code,
            "error": None,
        }
    except Exception as e:
        return {"stdout": "", "stderr": "", "returncode": -1, "error": str(e)}
