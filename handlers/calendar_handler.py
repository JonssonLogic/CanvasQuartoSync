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
            # Fallback: try to get it from course if available (internal)
            if hasattr(course, '_requester'):
                # Not easy to reconstitute Canvas object from just requester without URL/Token
                pass
            print("    ! Error: Canvas object required for calendar sync.")
            return

        with open(file_path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)
            
        events_config = data.get('events', [])
        if not events_config:
            print("  No events found in schedule.yaml.")
            return

        print("  (Note: Duplication check is minimal in this version)")

        for event_def in events_config:
            if 'days' in event_def:
                self._handle_recurring_series(course, event_def, canvas_obj)
            else:
                self._create_single_event(course, event_def, canvas_obj)

    def _create_single_event(self, course, event_data: dict, canvas_obj, specific_date=None):
        """
        Creates a single calendar event.
        """
        title = event_data.get('title', 'Untitled Event')
        
        if specific_date:
            date_str = specific_date.strftime('%Y-%m-%d')
        else:
            date_str = event_data.get('date')

        time_range = event_data.get('time', '12:00-13:00')
        start_time_str, end_time_str = time_range.split('-')
        
        start_at = f"{date_str}T{start_time_str.strip()}:00"
        end_at = f"{date_str}T{end_time_str.strip()}:00"

        location = event_data.get('location', '')
        description = event_data.get('description', '')

        event_payload = {
            'context_code': f"course_{course.id}",
            'title': title,
            'start_at': start_at,
            'end_at': end_at,
            'location_name': location,
            'description': description
        }

        try:
            # Use canvas object directly
            new_event = canvas_obj.create_calendar_event(calendar_event=event_payload)
            print(f"    + Created event: {title} on {date_str}")
            return new_event
        except Exception as e:
            print(f"    ! Error creating event {title}: {e}")

    def _handle_recurring_series(self, course, series_def: dict, canvas_obj):
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
                self._create_single_event(course, series_def, canvas_obj, specific_date=current_date)
                count += 1
            current_date += datetime.timedelta(days=1)
        
        print(f"  -> Generated {count} events for series.")
