import cortex_common


def test_extract_json_pulls_first_json_object():
    text = 'Here is your answer:\n{"a": 1, "b": [2, 3]}\nThanks.'
    assert cortex_common.extract_json(text) == {"a": 1, "b": [2, 3]}


def test_extract_json_raises_without_json():
    try:
        cortex_common.extract_json("no json here")
        assert False, "expected ValueError"
    except ValueError:
        pass


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def collect(self):
        return self._rows


class _FakeSession:
    def __init__(self, response_text):
        self.response_text = response_text
        self.last_sql = None
        self.last_params = None

    def sql(self, query, params=None):
        self.last_sql = query
        self.last_params = params
        return _FakeResult([{"RESP": self.response_text}])


def test_complete_calls_cortex_with_model_and_prompt_and_returns_text():
    session = _FakeSession("hello from cortex")

    result = cortex_common.complete(session, "claude-4-sonnet", "say hi")

    assert result == "hello from cortex"
    assert session.last_params == ["claude-4-sonnet", "say hi"]
    assert "snowflake.cortex.complete" in session.last_sql
