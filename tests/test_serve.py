"""내 PC 서버(agent.serve) 테스트. 실제 Agent 대신 가짜 작업을 끼워 넣고, 진짜 HTTP 요청으로 확인한다."""

import http.client
import json
import threading
import time

import pytest

from agent import build_site
from agent.serve import REQUEST_HEADER, Job, make_server


@pytest.fixture
def server(tmp_path):
    site = build_site.build(tmp_path / "data", tmp_path / "site", local=True)
    gate = threading.Event()
    calls = []

    def fake_run():
        calls.append("run")
        gate.wait(5)  # 테스트가 풀어줄 때까지 '실행 중' 상태 유지
        return {"new_issues": 2, "summarized": 2, "cost_usd": 0.001, "alerts": []}

    job = Job(tmp_path / "data", site, run_agent=fake_run, build=lambda: calls.append("build"))
    srv = make_server(job, site, port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv, job, gate, calls
    gate.set()
    srv.shutdown()
    srv.server_close()


def request(srv, method, path, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
    conn.request(method, path, body="{}" if method == "POST" else None, headers=headers or {})
    res = conn.getresponse()
    body = res.read()
    conn.close()
    return res.status, body


def ok_headers(srv, **extra):
    port = srv.server_address[1]
    return {"Host": f"localhost:{port}", "Origin": f"http://localhost:{port}", REQUEST_HEADER: "1", **extra}


def wait_until(predicate, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_serves_local_page(server):
    srv, *_ = server
    status, body = request(srv, "GET", "/", {"Host": "localhost"})
    assert status == 200 and b'id="refresh"' in body
    status, body = request(srv, "GET", "/data/meta.json", {"Host": "127.0.0.1"})
    assert json.loads(body)["mode"] == "local"


def test_update_runs_once_at_a_time_and_reports_status(server):
    srv, job, gate, calls = server
    status, body = request(srv, "POST", "/api/update", ok_headers(srv))
    assert status == 202 and json.loads(body)["running"] is True
    status, _ = request(srv, "POST", "/api/update", ok_headers(srv))
    assert status == 409  # 실행 중에는 또 시작하지 않음
    gate.set()
    assert wait_until(lambda: not job.status()["running"])
    status, body = request(srv, "GET", "/api/status", {"Host": "localhost"})
    result = json.loads(body)
    assert result["ok"] is True and "새 이슈 2개" in result["message"]
    assert calls == ["run", "build"]


@pytest.mark.parametrize("change", [
    {REQUEST_HEADER: ""},                         # 이 페이지의 버튼이 아닌 요청 (일반 폼 제출 등)
    {"Origin": "https://evil.example"},           # 다른 웹사이트에서 보낸 요청
    {"Origin": "http://localhost:9999"},          # 같은 PC의 다른 서버에서 보낸 요청
    {"Origin": "null"},                           # 샌드박스 iframe 등
    {"Origin": "http://localhost:abc"},           # 잘못된 포트 (서버가 죽지 않고 거부해야 함)
    {"Origin": "http://localhost:99999"},
    {"Host": "evil.example:8000"},                # DNS 재바인딩
])
def test_update_rejects_requests_not_from_this_page(server, change):
    srv, job, _, calls = server
    headers = {k: v for k, v in {**ok_headers(srv), **change}.items() if v != ""}
    status, _ = request(srv, "POST", "/api/update", headers)
    assert status == 403
    assert calls == [] and job.status()["running"] is False


def test_origin_with_https_scheme_is_rejected(server):
    srv, job, _, calls = server
    port = srv.server_address[1]
    status, _ = request(srv, "POST", "/api/update", ok_headers(srv, Origin=f"https://localhost:{port}"))
    assert status == 403 and calls == []


def test_pages_cannot_be_framed(server):
    # 다른 사이트가 이 페이지를 iframe에 넣고 버튼을 누르게 하는 클릭재킹 방지
    srv, *_ = server
    conn = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
    conn.request("GET", "/", headers={"Host": "localhost"})
    res = conn.getresponse()
    res.read()
    conn.close()
    assert res.getheader("X-Frame-Options") == "DENY"
    assert "frame-ancestors 'none'" in res.getheader("Content-Security-Policy")


def test_second_server_on_same_port_fails(server, tmp_path):
    srv, job, *_ = server
    with pytest.raises(OSError):
        make_server(job, tmp_path, srv.server_address[1])


def test_status_includes_alerts(tmp_path):
    job = Job(tmp_path, tmp_path, build=lambda: None,
              run_agent=lambda: {"new_issues": 0, "summarized": 0, "cost_usd": 0.0,
                                 "alerts": ["AI 중단: OPENAI_API_KEY가 설정되지 않음"]})
    assert job.start()
    assert wait_until(lambda: not job.status()["running"])
    assert job.status()["ok"] is True and job.status()["alerts"] == ["AI 중단: OPENAI_API_KEY가 설정되지 않음"]


def test_get_with_foreign_host_is_rejected(server):
    srv, *_ = server
    status, _ = request(srv, "GET", "/api/status", {"Host": "evil.example"})
    assert status == 403


def test_failed_run_is_reported(tmp_path):
    def broken():
        raise RuntimeError("크레딧 부족")

    job = Job(tmp_path, tmp_path, run_agent=broken, build=lambda: None)
    assert job.start()
    assert wait_until(lambda: not job.status()["running"])
    assert job.status()["ok"] is False and "크레딧 부족" in job.status()["message"]
