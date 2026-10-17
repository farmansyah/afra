"""System notifications from the background server (works with no A.F.R.A page open): macOS, Windows, Linux."""
import os
import subprocess
import sys


def _ps_quote(s: str) -> str:
    return s.replace("'", "''")


def show(title: str, body: str):
    if os.environ.get("AFRA_NO_NOTIFY"):  # tests
        print("notify:", title, body, flush=True)
        return
    try:
        if sys.platform == "darwin":
            esc = lambda s: s.replace("\\", "\\\\").replace('"', '\\"')
            subprocess.Popen(["osascript", "-e", f'display notification "{esc(body)}" with title "{esc(title)}" sound name "Glass"'],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif os.name == "nt":
            from xml.sax.saxutils import escape
            xml = f"<toast><visual><binding template='ToastGeneric'><text>{escape(title)}</text><text>{escape(body)}</text></binding></visual></toast>"
            script = ("[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null;"
                      "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] > $null;"
                      f"$x = New-Object Windows.Data.Xml.Dom.XmlDocument; $x.LoadXml('{_ps_quote(xml)}');"
                      "$app = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe';"
                      "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show([Windows.UI.Notifications.ToastNotification]::new($x))")
            subprocess.Popen(["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", script],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=0x08000000)  # CREATE_NO_WINDOW
        else:
            subprocess.Popen(["notify-send", title, body], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass  # a missing notification never breaks the chat
