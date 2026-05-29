from pathlib import Path

from ygotrainingbot.ydk import write_ydk


def test_write_ydk_sections(tmp_path: Path) -> None:
    path = tmp_path / "test.ydk"
    write_ydk(path, [123, 456], extra=[789], side=[])
    text = path.read_text(encoding="utf-8")
    assert "#main" in text
    assert "123" in text
    assert "#extra" in text
    assert "789" in text
    assert "#side" in text
