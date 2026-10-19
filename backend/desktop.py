"""Windows trayless local launcher. No model requests and no credential arguments."""
import argparse
from contextlib import contextmanager
import ctypes
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
import uuid
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / 'dev_data' / 'desktop'


@contextmanager
def file_lock(path):
    import msvcrt
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open('a+b')
    if os.fstat(handle.fileno()).st_size == 0:
        handle.write(b'0')
        handle.flush()
    handle.seek(0)
    acquired = False
    try:
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            acquired = True
        except OSError:
            pass
        yield acquired
    finally:
        if acquired:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        handle.close()


def paths(port):
    return (RUNTIME / f'server-{port}.lock', RUNTIME / f'server-{port}.json',
            RUNTIME / f'stop-{port}.json')


def read_json(path):
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def write_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value), encoding='utf-8')
    temporary.replace(path)


def available(port):
    with socket.socket() as connection:
        try:
            connection.bind(('127.0.0.1', port))
            return True
        except OSError:
            return False


def worker_active(port):
    lock, _, _ = paths(port)
    with file_lock(lock) as acquired:
        return not acquired


def health(port):
    from urllib.request import build_opener, ProxyHandler
    try:
        # Local-only, bypass proxy settings and never attach credentials.
        with build_opener(ProxyHandler({})).open(f'http://127.0.0.1:{port}/api/status', timeout=1) as response:
            value = json.load(response)
            return response.status == 200 and 'model_configured' in value
    except (OSError, ValueError):
        return False


def start(port, browser=True):
    if worker_active(port):
        if health(port):
            if browser:
                webbrowser.open(f'http://127.0.0.1:{port}/')
            return '后台服务已在运行，已复用现有服务。'
        raise RuntimeError('后台服务正在启动或停止，请稍后再次点击。')
    if not available(port):
        raise RuntimeError(f'端口 {port} 被其他服务占用。未停止或接管该程序。')
    from backend.model_config import load_environment, provider_options
    env = dict(os.environ)
    # Always use the project's saved provider, independent of the launching shell.
    for key in ('MODEL_API_KEY', 'MODEL_NAME', 'MODEL_BASE_URL'):
        env.pop(key, None)
    load_environment(ROOT, env)
    if not env.get('MODEL_API_KEY', '').strip():
        raise RuntimeError('请先在项目 .env 中填写 MODEL_API_KEY，再双击启动。')
    if not env.get('MODEL_NAME'):
        raise RuntimeError('请先在项目 .env 中填写 MODEL_NAME，再双击启动。')
    provider_options(env['MODEL_NAME'], env['MODEL_BASE_URL'])
    if not (ROOT / 'dist' / 'index.html').is_file():
        raise RuntimeError('未找到前端构建 dist/index.html，请先完成项目安装和构建。')
    pythonw = ROOT / '.venv' / 'Scripts' / 'pythonw.exe'
    if not pythonw.is_file():
        raise RuntimeError('未找到项目 .venv/Scripts/pythonw.exe。')
    # Child reads credentials from the backend .env; never command-line arguments.
    child_env = dict(os.environ)
    for key in ('MODEL_API_KEY', 'MODEL_NAME', 'MODEL_BASE_URL'):
        child_env.pop(key, None)
    with (RUNTIME / f'service-{port}.log').open('a', encoding='utf-8') as log:
        process = subprocess.Popen([str(pythonw), '-m', 'backend.desktop', 'serve', '--port', str(port)],
            cwd=ROOT, env=child_env, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP)
    for _ in range(120):
        if process.poll() is not None:
            raise RuntimeError(f'后台服务启动失败，请查看 dev_data/desktop/service-{port}.log。')
        if worker_active(port) and health(port):
            if browser:
                webbrowser.open(f'http://127.0.0.1:{port}/')
            return '后台服务已启动，可以关闭启动窗口。'
        time.sleep(0.25)
    raise RuntimeError('服务仍在启动，请稍后重试；重复启动不会创建第二个后台服务。')


def stop(port):
    if not worker_active(port):
        return '一键入口启动的后台服务已停止。其他启动方式的服务不受影响。'
    _, state_path, stop_path = paths(port)
    state = read_json(state_path)
    if not isinstance(state.get('token'), str) or not state['token']:
        raise RuntimeError('服务正在初始化，请稍后再次点击停止。')
    # A token-addressed local signal, never taskkill or a guessed PID.
    write_json(stop_path, {'token': state['token']})
    for _ in range(120):
        if not worker_active(port):
            return '后台服务已停止。论文和已保存报告仍保留。'
        time.sleep(0.25)
    return '停止信号已发送，服务正在结束当前本地请求，请稍候。'


def serve(port):
    import uvicorn
    lock_path, state_path, stop_path = paths(port)
    with file_lock(lock_path) as acquired:
        if not acquired:
            return
        token = uuid.uuid4().hex
        write_json(state_path, {'pid': os.getpid(), 'token': token, 'port': port, 'profile': 'default'})
        server = uvicorn.Server(uvicorn.Config('backend.app:app', host='127.0.0.1', port=port,
            log_level='warning', access_log=False, timeout_graceful_shutdown=20))
        finished = threading.Event()

        def watch_stop():
            while not finished.wait(0.25):
                if read_json(stop_path).get('token') == token:
                    server.should_exit = True
                    return

        threading.Thread(target=watch_stop, daemon=True).start()
        try:
            server.run()
        finally:
            finished.set()
            if read_json(state_path).get('token') == token:
                state_path.unlink(missing_ok=True)
            if read_json(stop_path).get('token') == token:
                stop_path.unlink(missing_ok=True)


def notify(message, error=False):
    ctypes.windll.user32.MessageBoxW(None, message, '研证 EvidenceMap', 0x10 if error else 0x40)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('start', 'stop', 'serve'))
    parser.add_argument('--port', type=int, default=8002)
    parser.add_argument('--quiet', action='store_true')
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error('Invalid port')
    RUNTIME.mkdir(parents=True, exist_ok=True)
    if args.action == 'serve':
        # pythonw leaves Python streams as None even with redirected OS handles.
        if sys.stdout is None or sys.stderr is None:
            stream = (RUNTIME / f'service-{args.port}.log').open('a', encoding='utf-8', buffering=1)
            sys.stdout = stream
            sys.stderr = stream
        serve(args.port)
        return 0
    try:
        with file_lock(RUNTIME / f'control-{args.port}.lock') as acquired:
            if not acquired:
                raise RuntimeError('上一项启动或停止操作尚未结束，请稍候。')
            message = start(args.port, not args.no_browser) if args.action == 'start' else stop(args.port)
        if args.quiet:
            print(message)
        elif args.action == 'stop':
            notify(message)
        return 0
    except Exception as error:
        # No raw exception/traceback: a provider or OS exception may contain private data.
        message = str(error) if isinstance(error, RuntimeError) else '操作失败，请检查项目环境、端口和本地配置。'
        if args.quiet:
            print(message)
        else:
            notify(message, error=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
