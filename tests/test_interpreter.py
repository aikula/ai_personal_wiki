
from app.core.interpreter import CodeInterpreter


class TestCodeInterpreter:
    def test_simple_execution(self, tmp_path):
        interp = CodeInterpreter(wiki_root=tmp_path)
        result = interp.execute('print("hello")')
        assert result.success is True
        assert result.stdout == "hello"

    def test_json_output_parsed(self, tmp_path):
        interp = CodeInterpreter(wiki_root=tmp_path)
        result = interp.execute('import json; print(json.dumps({"count": 42}))')
        assert result.success is True
        assert result.result_json == {"count": 42}

    def test_wiki_root_injected(self, tmp_path):
        interp = CodeInterpreter(wiki_root=tmp_path)
        result = interp.execute("print(WIKI_ROOT)")
        assert result.success is True
        assert str(tmp_path) in result.stdout

    def test_syntax_error(self, tmp_path):
        interp = CodeInterpreter(wiki_root=tmp_path)
        result = interp.execute("print('unclosed")
        assert result.success is False
        assert result.stderr != ""

    def test_timeout(self, tmp_path):
        interp = CodeInterpreter(wiki_root=tmp_path, timeout=2)
        result = interp.execute("import time; time.sleep(10)")
        assert result.success is False
        assert "timeout" in result.stderr.lower()

    def test_to_dict(self, tmp_path):
        interp = CodeInterpreter(wiki_root=tmp_path)
        result = interp.execute('print("ok")')
        d = result.to_dict()
        assert "stdout" in d
        assert "success" in d
        assert "result" in d
