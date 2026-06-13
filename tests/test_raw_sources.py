"""Tests for raw source helpers."""

from app.core.raw_sources import infer_project_from_raw_relative_path


class TestInferProject:
    def test_infer_project_with_subdir(self):
        assert infer_project_from_raw_relative_path("eywa-demo/bad.pdf") == "eywa-demo"

    def test_infer_project_root(self):
        assert infer_project_from_raw_relative_path("bad.pdf") == "_general"

    def test_infer_project_nested(self):
        assert infer_project_from_raw_relative_path("a/b/c.md") == "a"

    def test_infer_project_windows_path(self):
        assert infer_project_from_raw_relative_path("myapp\\docs\\readme.md") == "myapp"
