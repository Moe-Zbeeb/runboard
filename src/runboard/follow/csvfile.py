import csv

from . import LineFollower, to_record


class CsvFollower(LineFollower):
    kind = "table"

    def __init__(self, path, state=None):
        super().__init__(path, state)
        self.header = (state or {}).get("header")

    def state(self):
        return {"offset": self.offset, "header": self.header}

    def reset(self):
        super().reset()
        self.header = None

    def poll(self):
        records = []
        for row in csv.reader(self.read_lines()):
            if self.header is None:
                self.header = row
                continue
            record = to_record({key: value for key, value in zip(self.header, row) if value.strip()})
            if record:
                records.append(record)
        return records
