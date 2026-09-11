import json
import tempfile
import unittest
from pathlib import Path

from datetime import datetime

from xibo_sync.calendar_data import (
    CALENDAR_COLUMNS,
    csv_bytes,
    dataset_rows_to_events,
    filter_events_by_retention,
    load_events,
)


FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "office_calendar_events_sample.json"


class CalendarDataTests(unittest.TestCase):
    def write_snapshot(self, directory: Path, name: str, events: list[dict]) -> None:
        (directory /
         name).write_text(json.dumps({"value": events}), encoding="utf-8")

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

            self.assertEqual(
                snapshot.name, "office_calendar_events_2026-08-19_13-10-01.json")
            self.assertEqual([row["eventIdentifier"]
                             for row in rows], ["active"])

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
            self.assertEqual(csv_text.splitlines()[0].split(
                ","), list(CALENDAR_COLUMNS))

    def test_keeps_recurring_instances_as_separate_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            self.write_snapshot(
                directory,
                "office_calendar_events_2026-08-19_13-10-01.json",
                [{"id": "instance-1"}, {"id": "instance-2"}],
            )

            _, rows = load_events(directory)

            self.assertEqual([row["eventIdentifier"]
                             for row in rows], ["instance-1", "instance-2"])

    def test_empty_value_array_produces_header_only_csv(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            self.write_snapshot(
                directory, "office_calendar_events_2026-08-19_13-10-01.json", [])

            _, rows = load_events(directory)

            self.assertEqual(rows, [])
            self.assertTrue(csv_bytes(rows).startswith(
                ",".join(CALENDAR_COLUMNS).encode("utf-8")))

    def test_real_shape_fixture_populates_every_dataset_column(self) -> None:
        snapshot, rows = load_events(FIXTURE_PATH)

        self.assertEqual(snapshot.name, FIXTURE_PATH.name)
        self.assertEqual(len(rows), 1)
        self.assertEqual(
            rows[0],
            {
                "eventIdentifier": "AAMkAGFjZDUxZWYtZDUxYi00YzQxLTg4YzAtc2FtcGxl",
                "icalUid": "040000008200E00074C5B7101A82E00800000000sample@example.com",
                "subject": "Sample planning meeting",
                "bodyPreview": "Synthetic calendar event for mapping tests.",
                "bodyHtml": "<html><body><p>Synthetic calendar event for mapping tests.</p></body></html>",
                "startDateTime": "2026-09-10T09:00:00.0000000",
                "startTimeZone": "Europe/Berlin",
                "endDateTime": "2026-09-10T10:00:00.0000000",
                "endTimeZone": "Europe/Berlin",
                "isAllDay": "false",
                "isCancelled": "false",
                "showAs": "busy",
                "type": "singleInstance",
                "location": "Sample conference room",
                "organizer": "Sample Organizer",
                "organizerEmail": "organizer@example.test",
                "webLink": "https://outlook.office.com/calendar/item/sample",
                "lastModifiedDateTime": "2026-09-09T08:00:00.0000000Z",
            },
        )

    def test_dataset_rows_round_trip_to_flat_events(self) -> None:
        _, rows = load_events(FIXTURE_PATH)

        events = dataset_rows_to_events(rows)

        self.assertEqual(events[0]["id"], rows[0]["eventIdentifier"])
        self.assertEqual(events[0]["startDateTime"], rows[0]["startDateTime"])
        self.assertEqual(events[0]["organizer"], rows[0]["organizer"])

    def test_retention_filter_drops_events_older_than_cutoff(self) -> None:
        now = datetime.fromisoformat("2026-09-10T12:00:00+00:00")
        events = [
            {"id": "recent", "endDateTime": "2026-09-09T12:00:00", "endTimeZone": "UTC"},
            {"id": "old", "endDateTime": "2026-08-10T11:59:59", "endTimeZone": "UTC"},
        ]

        retained = filter_events_by_retention(events, 31, "UTC", now=now)

        self.assertEqual([event["id"] for event in retained], ["recent"])


if __name__ == "__main__":
    unittest.main()
