import json
import tempfile
import unittest
from pathlib import Path

from xibo_sync.calendar_data import CALENDAR_COLUMNS, csv_bytes, load_events


class CalendarDataTests(unittest.TestCase):
    def write_snapshot(self, directory: Path, name: str, events: list[dict]) -> None:
        (directory / name).write_text(json.dumps({"value": events}), encoding="utf-8")

    def test_selects_latest_snapshot_and_filters_cancelled_events(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            self.write_snapshot(
                directory,
                "office_calendar_events_2026-08-18_13-10-01.json",
                [{"id": "old"}],
            )
            self.write_snapshot(
                directory,
                "office_calendar_events_2026-08-19_13-10-01.json",
                [
                    {"id": "active", "subject": "Latest"},
                    {"id": "cancelled", "isCancelled": True},
                ],
            )

            snapshot, rows = load_events(directory)

            self.assertEqual(snapshot.name, "office_calendar_events_2026-08-19_13-10-01.json")
            self.assertEqual([row["eventIdentifier"] for row in rows], ["active"])

    def test_flattens_nested_fields_and_csv_quotes_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            self.write_snapshot(
                directory,
                "office_calendar_events_2026-08-19_13-10-01.json",
                [{
                    "id": "event-1",
                    "iCalUId": "ical-1",
                    "subject": "Planning, phase 1",
                    "bodyPreview": "Line 1\nLine 2",
                    "body": {"content": "<p>HTML</p>"},
                    "start": {"dateTime": "2026-08-20T09:00:00", "timeZone": "UTC"},
                    "end": {"dateTime": "2026-08-20T10:00:00", "timeZone": "UTC"},
                    "location": {"displayName": "Room A"},
                    "organizer": {"emailAddress": {"name": "Alex", "address": "alex@example.com"}},
                }],
            )

            _, rows = load_events(directory, include_cancelled=True)
            csv_text = csv_bytes(rows).decode("utf-8")

            self.assertEqual(rows[0]["bodyHtml"], "<p>HTML</p>")
            self.assertIn('"Planning, phase 1"', csv_text)
            self.assertIn('"Line 1\nLine 2"', csv_text)
            self.assertEqual(csv_text.splitlines()[0].split(","), list(CALENDAR_COLUMNS))

    def test_keeps_recurring_instances_as_separate_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            self.write_snapshot(
                directory,
                "office_calendar_events_2026-08-19_13-10-01.json",
                [{"id": "instance-1"}, {"id": "instance-2"}],
            )

            _, rows = load_events(directory)

            self.assertEqual([row["eventIdentifier"] for row in rows], ["instance-1", "instance-2"])

    def test_empty_value_array_produces_header_only_csv(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            self.write_snapshot(directory, "office_calendar_events_2026-08-19_13-10-01.json", [])

            _, rows = load_events(directory)

            self.assertEqual(rows, [])
            self.assertTrue(csv_bytes(rows).startswith(",".join(CALENDAR_COLUMNS).encode("utf-8")))


if __name__ == "__main__":
    unittest.main()
