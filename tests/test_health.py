import json

from agent.health import check


def test_check(tmp_path):
    path = tmp_path / "runs.json"
    assert check(path) == []  # 실행 기록이 아직 없으면 정상
    path.write_text(json.dumps([{"alerts": ["AI 중단"]}, {"alerts": []}]), encoding="utf-8")
    assert check(path) == []  # 마지막 실행만 봄
    path.write_text(json.dumps([{"alerts": []}, {"alerts": ["매체 과반 수집 실패"]}]), encoding="utf-8")
    assert check(path) == ["매체 과반 수집 실패"]
