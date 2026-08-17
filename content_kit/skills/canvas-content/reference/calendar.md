# Calendar events (`schedule.yaml`)

A single `schedule.yaml` at the **content root** defines course calendar events.

Calendar sync is **opt-in**: it only runs when the developer passes `--sync-calendar`.
Editing this file has no effect on an ordinary sync, so mention it explicitly when you
change it.

```yaml
events:
  - title: "Course Kickoff"
    date: "2026-01-19"
    time: "09:00-10:00"
    location: "Room E1405"
    description: "Introduction and group formation."

  - title: "Lecture: Statics"
    start_date: "2026-01-20"
    end_date: "2026-03-10"
    days: ["Mon", "Wed"]
    time: "10:15-12:00"
    location: "Room E1405"
```

## Single events

| Key | Required | Notes |
|---|---|---|
| `title` | yes | Event name |
| `date` | yes | `YYYY-MM-DD` |
| `time` | no | `HH:MM-HH:MM`, defaults to `12:00-13:00` |
| `location` | no | Maps to the Canvas location field |
| `description` | no | Plain text |

## Recurring series

Add `days` and the event becomes a series, expanded into one Canvas event per matching
date between `start_date` and `end_date`:

| Key | Required | Notes |
|---|---|---|
| `start_date` / `end_date` | yes | `YYYY-MM-DD`, inclusive |
| `days` | yes | Any of `Mon`, `Tue`, `Wed`, `Thu`, `Fri`, `Sat`, `Sun` |

`title`, `time`, `location`, and `description` apply to every occurrence.

## Behaviour worth knowing

- **Times are course-local.** A `10:15` start means 10:15 to students, converted to the
  right instant at sync time. A recurring series that spans a daylight-saving change
  keeps its local time on both sides - every occurrence is converted individually.
- **Duplicates are skipped, not updated.** An event matching an existing one by title,
  date, start time, and location is left alone. Changing a *time* creates a second event
  rather than moving the first - the old one must be deleted in Canvas by hand.
- Events are never deleted by the sync.
