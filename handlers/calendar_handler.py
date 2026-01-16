import yaml
import datetime
from canvasapi import Canvas
from handlers.base_handler import BaseHandler

class CalendarHandler(BaseHandler):
    def can_handle(self, file_path: str) -> bool:
        return file_path.endswith('schedule.yaml')

    def sync(self, file_path: str, course, module=None, canvas_obj=None):
        print(f"Processing Calendar Schedule: {file_path}")
        
        # We need the canvas object to create events with context_code
        if not canvas_obj:
            print("    ! Error: Canvas object required for calendar sync.")
            return

        # 1. Fetch existing events for this course to prevent duplication
        print("  Fetching existing calendar events...")
        try:
            # Note: we filter by context_code to only get events for this course
            existing_events = list(canvas_obj.get_calendar_events(
                context_codes=[f"course_{course.id}"],
                all_events=True
            ))
            print(f"    Found {len(existing_events)} existing events.")
        except Exception as e:
            print(f"    ! Error fetching existing events: {e}")
            existing_events = []

        with open(file_path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)
            
        events_config = data.get('events', [])
        if not events_config:
            print("  No events found in schedule.yaml.")
            return

        for event_def in events_config:
            if 'days' in event_def:
                self._handle_recurring_series(course, event_def, canvas_obj, existing_events)
            else:
                self._create_single_event(course, event_def, canvas_obj, existing_events)

    def _create_single_event(self, course, event_data: dict, canvas_obj, existing_events, specific_date=None):
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
        
        start_at = f"{date_str}T{start_time_str.strip()}:00Z" # Assuming UTC for simplicity or following Canvas format
        end_at = f"{date_str}T{end_time_str.strip()}:00Z"

        location = event_data.get('location', '')
        description = event_data.get('description', '')

        # Duplicate Check
        # We compare Title, Start Time, and Location
        for ex in existing_events:
            # Canvas might return times with Z or offset, and might have slight formatting differences
            # We do a basic string match on the relevant parts
            if ex.title == title and date_str in ex.start_at and start_time_str.strip() in ex.start_at:
                if location == getattr(ex, 'location_name', ''):
                    print(f"    - Event already exists: {title} on {date_str} (Skipping)")
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
            print(f"    + Created event: {title} on {date_str}")
            # Add to local list to prevent duplicates within the same run (e.g. overlapping series)
            existing_events.append(new_event)
            return new_event
        except Exception as e:
            print(f"    ! Error creating event {title}: {e}")

    def _handle_recurring_series(self, course, series_def: dict, canvas_obj, existing_events):
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
        
        print(f"  Expanding series '{title}' from {start_str} to {end_str}...")

        current_date = start_date
        count = 0
        while current_date <= end_date:
            if current_date.weekday() in target_weekdays:
                self._create_single_event(course, series_def, canvas_obj, existing_events, specific_date=current_date)
                count += 1
            current_date += datetime.timedelta(days=1)
        
        print(f"  -> Processed {count} dates for series.")
