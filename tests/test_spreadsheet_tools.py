from pmagent.tools.spreadsheet_tools import apply_approved_requests, propose_cell_update


class FakeWorkbook:
    def __init__(self):
        self.cells = {("Control Page", "A160"): ""}
        self.rows = []
        self.writes = []

    def get_cell(self, sheet, cell):
        return self.cells[(sheet, cell)]

    def set_cell(self, sheet, cell, value):
        self.writes.append((sheet, cell, value))
        self.cells[(sheet, cell)] = value

    def append_request(self, row):
        self.rows.append(row)

    def requests(self):
        return list(enumerate(self.rows))

    def replace_request(self, index, row):
        self.rows[index] = row


def test_approved_control_page_update_is_applied():
    workbook = FakeWorkbook()
    request_id = propose_cell_update(
        workbook, "Control Page", "A160", "CODEX_ACCESS_TEST_2026-07-18"
    )
    workbook.rows[0][6] = "Approved"

    assert apply_approved_requests(workbook) == [request_id]
    assert workbook.writes == [
        ("Control Page", "A160", "CODEX_ACCESS_TEST_2026-07-18")
    ]
    assert workbook.rows[0][6] == "Applied"


def test_pending_update_never_changes_the_target_cell():
    workbook = FakeWorkbook()
    propose_cell_update(workbook, "Control Page", "A160", "CODEX_ACCESS_TEST_2026-07-18")

    assert apply_approved_requests(workbook) == []
    assert workbook.writes == []
