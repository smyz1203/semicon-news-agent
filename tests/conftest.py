import sys
from pathlib import Path

# 저장소 루트에서 `pytest`만 실행해도 agent 패키지를 찾을 수 있게
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
