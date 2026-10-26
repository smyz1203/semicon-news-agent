"""내 PC(localhost)에서 Agent와 웹페이지를 함께 실행한다. GitHub 없이 동작한다.

사용법:
    python -m agent.serve                  # http://localhost:8000 을 열고, 새로고침 버튼으로 내 PC에서 실행
    python -m agent.serve --every 180      # 켜 두는 동안 180분마다 자동 실행
    python -m agent.serve --run-now        # 켜자마자 한 번 실행
    python -m agent.serve --data-dir data  # 저장소의 data/ 사용 (기본은 local-data/ → GitHub에 올라가지 않음)

안전장치:
    - 이 컴퓨터(127.0.0.1)에서만 접속 가능
    - 실행 요청(POST /api/update)은 이 페이지가 보내는 전용 헤더가 있고, 주소가 localhost일 때만 받음
      (다른 웹사이트가 몰래 실행을 요청해 AI 비용을 쓰게 하는 것을 막기 위함)
    - 한 번에 하나만 실행
"""

import argparse
import json
import logging
import os
import socket
import threading
import time
import webbrowser
from datetime import datetime, timezone
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv

from agent import build_site
from agent import main as agent_main
from agent.config import ROOT
from agent.store import to_iso

log = logging.getLogger("agent.serve")

LOCAL_DATA = ROOT / "local-data"
LOCAL_SITE = ROOT / "local-site"
REQUEST_HEADER = "X-Agent-Request"   # 웹페이지의 새로고침 버튼만 붙이는 헤더
LOCAL_HOSTS = {"localhost", "127.0.0.1", "[::1]", "::1"}


class Job:
    """Agent 실행 한 건의 상태. 한 번에 하나만 실행한다."""

    def __init__(self, data_dir: Path, site_dir: Path, run_agent=None, build=None):
        self._lock = threading.Lock()
        self._run_agent = run_agent or (lambda: agent_main.run(data_dir=data_dir))
        self._build = build or (lambda: build_site.build(data_dir, site_dir, local=True))
        self.running = False
        self.started_at = self.finished_at = None
        self.ok: bool | None = None
        self.message = ""
        self.alerts: list[str] = []

    def start(self) -> bool:
        """실행을 시작한다. 이미 실행 중이면 False."""
        with self._lock:
            if self.running:
                return False
            self.running, self.ok, self.message, self.alerts = True, None, "실행 중", []
            self.started_at, self.finished_at = _now(), None
        threading.Thread(target=self._work, name="agent-run", daemon=True).start()
        return True

    def status(self) -> dict:
        with self._lock:
            return {"running": self.running, "started_at": self.started_at, "finished_at": self.finished_at,
                    "ok": self.ok, "message": self.message, "alerts": list(self.alerts)}

    def _work(self) -> None:
        ok, message, alerts = False, "", []
        try:
            record = self._run_agent()
            self._build()
            ok = True
            if record:
                message = (f"새 이슈 {record['new_issues']}개, 요약 {record['summarized']}건, "
                           f"추정 비용 ${record['cost_usd']:.4f}")
                alerts = list(record.get("alerts") or [])
        except SystemExit as e:  # 설정 오류 등으로 main.run이 멈춘 경우
            message = str(e) or "설정 오류로 중단됨"
            log.error("Agent 실행 중단: %s", message)
        except Exception as e:
            log.exception("Agent 실행 실패")
            message = f"{type(e).__name__}: {str(e)[:200]}"
        finally:
            with self._lock:
                self.running, self.ok, self.message, self.alerts = False, ok, message, alerts
                self.finished_at = _now()
        log.info("실행 %s: %s", "완료" if ok else "실패", message)


class Handler(SimpleHTTPRequestHandler):
    """local-site/ 파일을 보여주고, /api/update · /api/status 요청을 처리한다."""

    job: Job  # make_server에서 채움

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")  # 내 PC에서는 항상 최신 파일을 보여줌
        # 다른 웹사이트가 이 페이지를 몰래 iframe에 넣고 새로고침 버튼을 누르게 하는 것(클릭재킹)을 막음
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "frame-ancestors 'none'")
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()

    def do_GET(self):
        if not self._host_ok():
            return self._json(403, {"error": "localhost로만 접속할 수 있습니다."})
        if urlsplit(self.path).path == "/api/status":
            return self._json(200, self.job.status())
        return super().do_GET()

    def do_HEAD(self):
        if not self._host_ok():
            return self._json(403, {"error": "localhost로만 접속할 수 있습니다."})
        return super().do_HEAD()

    def do_POST(self):
        if urlsplit(self.path).path != "/api/update":
            return self._json(404, {"error": "없는 주소입니다."})
        if not (self._host_ok() and self._origin_ok() and self.headers.get(REQUEST_HEADER) == "1"):
            return self._json(403, {"error": "이 페이지의 새로고침 버튼으로만 실행할 수 있습니다."})
        started = self.job.start()
        return self._json(202 if started else 409, {"started": started, **self.job.status()})

    # ── 안전 확인 ────────────────────────────────────────────

    def _host_ok(self) -> bool:
        # Host 헤더가 localhost가 아니면 거부 (다른 사이트 주소로 이 서버에 접근하는 DNS 재바인딩 공격 방지)
        host = self.headers.get("Host", "")
        return _hostname(host) in LOCAL_HOSTS

    def _origin_ok(self) -> bool:
        # 브라우저가 보낸 요청이면 Origin이 이 서버 주소여야 함 (다른 웹사이트에서 보낸 요청 거부)
        origin = self.headers.get("Origin")
        if origin is None:
            return True
        try:
            parts = urlsplit(origin)
            port = parts.port or 80  # 브라우저는 기본 포트(80)를 Origin에서 생략함
        except ValueError:  # 숫자가 아니거나 범위를 벗어난 포트
            return False
        return parts.scheme == "http" and parts.hostname in LOCAL_HOSTS and port == self.server.server_address[1]

    # ── 응답 ────────────────────────────────────────────────

    def _json(self, status: int, body: dict) -> None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def log_message(self, fmt, *args):  # 요청마다 찍히는 로그는 조용히
        log.debug("%s - %s", self.address_string(), fmt % args)


class LocalServer(ThreadingHTTPServer):
    """같은 포트에 서버가 두 개 뜨지 않게 함 (윈도는 기본 설정으로는 같은 포트를 또 열 수 있음)."""

    allow_reuse_address = os.name != "nt"
    daemon_threads = True

    def server_bind(self):
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def make_server(job: Job, site_dir: Path, port: int) -> LocalServer:
    handler = type("BoundHandler", (Handler,), {"job": job})
    return LocalServer(("127.0.0.1", port), partial(handler, directory=str(site_dir)))


def _hostname(host_header: str) -> str:
    if host_header.startswith("["):  # [::1]:8000
        return host_header.split("]")[0] + "]"
    return host_header.rsplit(":", 1)[0].lower()


def _now() -> str:
    return to_iso(datetime.now(timezone.utc))


def _schedule(job: Job, minutes: int) -> None:
    while True:
        time.sleep(minutes * 60)
        if job.start():
            log.info("예약 실행 시작 (%d분마다)", minutes)


def main() -> None:
    parser = argparse.ArgumentParser(description="내 PC에서 뉴스 Agent와 웹페이지 실행")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--data-dir", type=Path, default=LOCAL_DATA, help="결과를 저장할 폴더 (기본: local-data)")
    parser.add_argument("--every", type=int, default=0, metavar="분", help="켜 두는 동안 몇 분마다 자동 실행할지 (0이면 끔)")
    parser.add_argument("--run-now", action="store_true", help="켜자마자 한 번 실행")
    parser.add_argument("--no-browser", action="store_true", help="브라우저를 자동으로 열지 않음")
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")  # OPENAI_API_KEY 또는 ANTHROPIC_API_KEY
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    for noisy in ("httpx", "httpx2", "openai", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    data_dir = args.data_dir.resolve()
    job = Job(data_dir, LOCAL_SITE)
    try:
        server = make_server(job, LOCAL_SITE, args.port)  # 포트부터 잡아서, 이미 켜진 서버가 있으면 바로 알림
    except OSError:
        raise SystemExit(f"포트 {args.port}를 이미 쓰고 있습니다. 이미 켜 둔 창이 있는지 확인하거나 "
                         f"--port {args.port + 1} 처럼 다른 포트를 쓰세요.")
    build_site.build(data_dir, LOCAL_SITE, local=True)  # 지금 있는 데이터로 먼저 페이지를 만들어 둠

    url = f"http://localhost:{args.port}/"
    log.info("웹페이지: %s  (데이터 폴더: %s, 끄려면 Ctrl+C)", url, data_dir)
    if args.every > 0:
        threading.Thread(target=_schedule, args=(job, args.every), daemon=True).start()
        log.info("%d분마다 자동 실행", args.every)
    if args.run_now:
        job.start()
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("종료")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
