"""Start the local app from Finder, reuse a running server, then open a browser."""
import os
from pathlib import Path
import socket
import signal
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import ProxyHandler, Request, build_opener
import webbrowser

APP_DIR = Path(__file__).resolve().parent
URL = 'http://localhost:8501'
HEALTH_URL = 'http://127.0.0.1:8501/_stcore/health'


def is_ready():
    try:
        with build_opener(ProxyHandler({})).open(Request(HEALTH_URL), timeout=1) as response:
            return response.status == 200 and response.read().strip() == b'ok'
    except (OSError, URLError):
        return False


def stop_app():
    """Stop only Streamlit listeners belonging to this project."""
    result = subprocess.run(['/usr/sbin/lsof', '-t', '-nP', '-iTCP:8501', '-sTCP:LISTEN'],
                            capture_output=True, text=True)
    pids = set(result.stdout.split())
    if not pids:
        print('Roberon HR Manager is already stopped.')
        return 0
    for pid in pids:
        command = subprocess.run(['/bin/ps', '-p', pid, '-o', 'command='],
                                 capture_output=True, text=True).stdout
        cwd = subprocess.run(['/usr/sbin/lsof', '-a', '-p', pid, '-d', 'cwd', '-Fn'],
                             capture_output=True, text=True).stdout.splitlines()
        project_cwd = 'n' + str(APP_DIR) in cwd
        if 'streamlit' not in command or ('run app.py' not in command and str(APP_DIR / 'app.py') not in command):
            print('Port 8501 is used by another service; it has been left running.')
            return 1
        if not project_cwd and str(APP_DIR / 'app.py') not in command:
            print('The running app belongs to another project; it has been left running.')
            return 1
        try:
            os.kill(int(pid), signal.SIGTERM)
        except ProcessLookupError:
            continue
    for _ in range(40):
        if not is_ready():
            print('Roberon HR Manager stopped. Your saved employees and payroll records are preserved.')
            return 0
        time.sleep(0.25)
    print('Shutdown requested. The app is still finishing; try again shortly.')
    return 1


def main():
    if '--stop' in sys.argv:
        return stop_app()
    if not is_ready():
        with socket.socket() as probe:
            if probe.connect_ex(('127.0.0.1', 8501)) == 0:
                print('Port 8501 is occupied by another service. Close that service and try again.')
                return 1
        log_path = APP_DIR / '.launcher.log'
        fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, 'ab') as log:
            process = subprocess.Popen(
                [sys.executable, '-m', 'streamlit', 'run', str(APP_DIR / 'app.py'),
                 '--server.address', 'localhost', '--server.port', '8501', '--server.headless', 'true'],
                cwd=APP_DIR, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                start_new_session=True,
            )
        print('Starting Roberon HR Manager...')
        for _ in range(60):
            if is_ready():
                break
            if process.poll() is not None:
                print(f'The app could not start. See {log_path} for details.')
                return 1
            time.sleep(0.5)
        else:
            print(f'Startup is taking longer than expected. Check {log_path}, then open {URL}.')
            return 1
    print(f'Roberon HR Manager is running at {URL}')
    if '--no-browser' not in sys.argv:
        if not webbrowser.open(URL, new=2):
            print(f'Open {URL} in your browser.')
    print('You can close this Terminal window. The app will keep running.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
