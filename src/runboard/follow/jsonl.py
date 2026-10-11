import json

from . import LineFollower, flatten, to_record


class JsonlFollower(LineFollower):
    kind = "table"

    def poll(self):
        records = []
        for line in self.read_lines():
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if isinstance(value, dict):
                record = to_record(flatten(value))
                if record:
                    records.append(record)
        return records
