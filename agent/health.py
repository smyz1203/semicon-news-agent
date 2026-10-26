"""마지막 실행에 경고가 있었는지 확인한다. 경고가 있으면 실패(종료 코드 1)로 끝나 GitHub이 메일로 알린다.

표준 라이브러리만 쓰므로 패키지 설치 없이 실행된다:
    python -m agent.health
"""

import json
import sys
from pathlib import Path

RUNS = Path(__file__).resolve().parent.parent / "data" / "runs.json"


def check(path: Path = RUNS) -> list[str]:
    if not path.exists():
        return []
    runs = json.loads(path.read_text(encoding="utf-8"))
    return runs[-1].get("alerts", []) if runs else []


def main() -> None:
    alerts = check()
    for alert in alerts:
        print(f"::error::{alert}")  # GitHub Actions 화면에 빨간 오류로 표시
    if alerts:
        sys.exit(1)
    print("정상")


if __name__ == "__main__":
    main()
