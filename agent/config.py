"""설정 파일(config/*.yaml)을 읽는다."""

from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
WEB_DIR = ROOT / "web"
SITE_DIR = ROOT / "_site"


@dataclass(frozen=True)
class Source:
    id: str
    name: str
    url: str
    lang: str
    filter: bool
    tz: str = "UTC"
    enabled: bool = True


@dataclass
class Config:
    settings: dict
    sources: list[Source]
    google_news: dict = field(default_factory=dict)


def load_config(config_dir: Path = CONFIG_DIR) -> Config:
    settings = _read_yaml(config_dir / "settings.yaml")
    raw = _read_yaml(config_dir / "sources.yaml")
    sources = [Source(**s) for s in raw["sources"]]
    return Config(
        settings=settings,
        sources=[s for s in sources if s.enabled],
        google_news=raw.get("google_news") or {},
    )


def _read_yaml(path: Path) -> dict:
    # utf-8-sig: Windows 메모장 등이 붙이는 BOM이 있어도 읽히게
    with open(path, encoding="utf-8-sig") as f:
        return yaml.safe_load(f)
