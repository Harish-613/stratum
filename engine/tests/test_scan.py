from stratum_engine.scan import scan_folder


def test_scan_finds_files_and_skips_venv(tmp_path):
    (tmp_path / "app.py").write_text("import utils\n")
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "utils.py").write_text("def helper():\n    pass\n")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "junk.py").write_text("import os\n")

    result = scan_folder(str(tmp_path))

    assert list(result) == ["app.py", "pkg/utils.py"]
    assert result["app.py"]["imports"] == ["utils"]
    assert result["pkg/utils.py"]["functions"] == ["helper"]