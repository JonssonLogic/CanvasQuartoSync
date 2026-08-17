import os
import yaml
import datetime
from canvasapi import Canvas
from handlers.base_handler import BaseHandler
from handlers.dates import parse_canvas_utc, resolve_timezone, to_canvas_iso
from handlers.log import logger

class CalendarHandler(BaseHandler):
    def can_handle(self, file_path: str) -> bool:
        return file_path.endswith('schedule.yaml')

    def sync(self, file_path: str, course, module=None, canvas_obj=None, content_root=None):
        logger.info("[cyan]Processing calendar schedule:[/cyan] %s", os.path.basename(file_path))

        # We need the canvas object to create events with context_code
        if not canvas_obj:
            logger.error("    Canvas object required for calendar sync")
            return

        # 1. Fetch existing events for this course to prevent duplication
        logger.debug("  Fetching existing calendar events...")
        try:
            # Note: we filter by context_code to only get events for this course
            existing_events = list(canvas_obj.get_calendar_events(
                context_codes=[f"course_{course.id}"],
                all_events=True
            ))
            logger.debug("    Found %d existing events", len(existing_events))
        except Exception as e:
            logger.error("    Failed to fetch existing events: %s", e)
            existing_events = []

        with open(file_path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)

        events_config = data.get('events', [])
        if not events_config:
            logger.warning("  No events found in schedule.yaml")
            return

        # Event times are course-local wall clock; each occurrence is converted
        # individually so a series spanning a DST change keeps its local time.
        tz = resolve_timezone(course, content_root)

        for event_def in events_config:
            if 'days' in event_def:
                self._handle_recurring_series(course, event_def, canvas_obj, existing_events, tz)
            else:
                self._create_single_event(course, event_def, canvas_obj, existing_events, tz=tz)

    def _create_single_event(self, course, event_data: dict, canvas_obj, existing_events, specific_date=None, tz=None):
        """
        Creates a single calendar event if it doesn't already exist.
        """
        title = event_data.get('title', 'Untitled Event')

        if specific_date:
            date_str = specific_date.strftime('%Y-%m-%d')
        else:
            date_str = event_data.get('date')

        time_range = event_data.get('time', '12:00-13:00')
        start_time_str, end_time_str = time_range.split('-')

        start_at = to_canvas_iso(f"{date_str}T{start_time_str.strip()}:00", tz, 'time')
        end_at = to_canvas_iso(f"{date_str}T{end_time_str.strip()}:00", tz, 'time')

        location = event_data.get('location', '')
        description = event_data.get('description', '')

        # Duplicate Check: compare Title, Start Instant, and Location.
        # Canvas always answers in UTC, so we compare the instants we computed
        # rather than matching local-time substrings against a UTC string.
        start_instant = parse_canvas_utc(start_at)
        for ex in existing_events:
            if ex.title != title:
                continue
            if parse_canvas_utc(getattr(ex, 'start_at', None)) != start_instant:
                continue
            if location == getattr(ex, 'location_name', ''):
                logger.debug("    Event already exists: %s on %s (skipping)", title, date_str)
                return

        event_payload = {
            'context_code': f"course_{course.id}",
            'title': title,
            'start_at': start_at,
            'end_at': end_at,
            'location_name': location,
            'description': description
        }

        try:
            new_event = canvas_obj.create_calendar_event(calendar_event=event_payload)
            logger.info("    [green]Created event:[/green] %s on %s", title, date_str)
            # Add to local list to prevent duplicates within the same run (e.g. overlapping series)
            existing_events.append(new_event)
            return new_event
        except Exception as e:
            logger.error("    Failed to create event %s: %s", title, e)

    def _handle_recurring_series(self, course, series_def: dict, canvas_obj, existing_events, tz=None):
        """
        Generates individual events from a recurrence series.
        """
        title = series_def.get('title')
        start_str = series_def.get('start_date')
        end_str = series_def.get('end_date')
        days_of_week = series_def.get('days', [])

        day_map = {"Mon": 0, "Tue": 1, "Wed": 2, "Thu": 3, "Fri": 4, "Sat": 5, "Sun": 6}
        target_weekdays = [day_map[d] for d in days_of_week if d in day_map]

        start_date = datetime.datetime.strptime(start_str, '%Y-%m-%d')
        end_date = datetime.datetime.strptime(end_str, '%Y-%m-%d')

        logger.info("  [cyan]Expanding series[/cyan] '%s' from %s to %s...", title, start_str, end_str)

        current_date = start_date
        count = 0
        while current_date <= end_date:
            if current_date.weekday() in target_weekdays:
                self._create_single_event(course, series_def, canvas_obj, existing_events,
                                          specific_date=current_date, tz=tz)
                count += 1
            current_date += datetime.timedelta(days=1)

        logger.debug("  Processed %d dates for series", count)
