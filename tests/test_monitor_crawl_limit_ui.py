from pathlib import Path


def test_monitor_ui_no_longer_exposes_crawl_limit_controls(project_root_path: Path):
    html = (project_root_path / "api" / "static" / "monitor.html").read_text(encoding="utf-8")

    assert "采集数量" not in html
    assert "crawlLimit" not in html
    assert "crawlLimitMode" not in html
    assert "crawlLimitHint" not in html
    assert "data-limit-option" not in html
    assert "max_notes_count" not in html


def test_monitor_help_no_longer_mentions_crawl_limit_presets(project_root_path: Path):
    html = (project_root_path / "api" / "static" / "monitor.html").read_text(encoding="utf-8")

    assert "50-300" not in html
    assert "300-1000" not in html
    assert "试跑数量" not in html
    assert "降低采集数量" not in html
