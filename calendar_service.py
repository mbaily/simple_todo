"""Validate calendar rules before saving and contain invalid historical rules."""
import logging
import re
from datetime import datetime, timedelta

from models import CalendarEvent

try:
    from dateutil.rrule import rrulestr
except ImportError:
    rrulestr = None

logger = logging.getLogger(__name__)

CALENDAR_SORT_OPTIONS = {
    'created_asc': 'Created: oldest first',
    'created_desc': 'Created: newest first',
    'title_asc': 'Title: A–Z',
    'title_desc': 'Title: Z–A',
    'modified_desc': 'Modified: newest first',
    'start_asc': 'Start date: oldest first',
}
DEFAULT_CALENDAR_SORT = 'created_asc'


def parse_calendar_start(start_at, start_time=None):
    """Store wall-clock calendar times without applying a timezone conversion."""
    try:
        start = datetime.fromisoformat(start_at)
        if start.tzinfo is not None:
            raise ValueError()
        if start_time is not None:
            start = start.replace(hour=0, minute=0, second=0, microsecond=0)
            if not start_time.strip():
                return start, True
            if not re.fullmatch(r'\d{2}:\d{2}', start_time):
                raise ValueError()
            hour, minute = map(int, start_time.split(':'))
            return start.replace(hour=hour, minute=minute), False
        # Older clients may submit either a date or a datetime-local value.
        return start, 'T' not in start_at and ' ' not in start_at
    except (ValueError, TypeError):
        raise ValueError('Invalid event date or start time') from None


def parse_calendar_duration(value, all_day=False):
    if value is None or value == '':
        return None
    if isinstance(value, bool) or not re.fullmatch(r'[0-9]+', str(value)) or not 1 <= int(value) <= 10080:
        raise ValueError('Duration must be a whole number of minutes between 1 and 10080.')
    return None if all_day else int(value)


def _layout_week_events(rows):
    """Assign overlapping blocks separate lanes, reusing space after each cluster."""
    rows.sort(key=lambda row: (row['start_minute'], row['event'].id, row['occurrence_at']))
    cluster, lane_ends = [], []

    def finish():
        for row in cluster:
            row['width'] = 100 / len(lane_ends)
            row['left'] = row['lane'] * row['width']

    for row in rows:
        if lane_ends and row['start_minute'] >= max(lane_ends):
            finish()
            cluster, lane_ends = [], []
        lane = next((index for index, end in enumerate(lane_ends) if end <= row['start_minute']), len(lane_ends))
        # Reserve half an hour of space so short events remain readable.
        visual_end = min(1440, max(row['end_minute'], row['start_minute'] + 30))
        if lane == len(lane_ends):
            lane_ends.append(visual_end)
        else:
            lane_ends[lane] = visual_end
        row.update(lane=lane, top=row['start_minute'] / 1440 * 100,
                   height=(visual_end - row['start_minute']) / 1440 * 100)
        cluster.append(row)
    if cluster:
        finish()


def calendar_week(events, start, completed, sort_order):
    """Split timed occurrences at midnight and lay out overlapping events."""
    days = [dict(date=start + timedelta(days=index), all_day=[], timed=[])
            for index in range(7)]
    end = start + timedelta(days=7)
    from config import CALENDAR_MAX_OCCURRENCES
    for event in events:
        duration = event.duration_minutes or 60
        search_start = start if event.all_day or not event.duration_minutes else start - timedelta(minutes=duration)
        for occurrence in calendar_occurrences(event, search_start, end - timedelta(microseconds=1), CALENDAR_MAX_OCCURRENCES):
            is_completed = (event.id, occurrence) in completed
            today = datetime.now().date()
            is_late = (occurrence.date() < today) and not is_completed
            is_important = is_late and bool(event.important_if_late)
            row = dict(event=event, occurrence_at=occurrence,
                       completed=is_completed, is_late=is_late, is_important=is_important)
            if event.all_day:
                days[(occurrence.date() - start.date()).days]['all_day'].append(row)
                continue
            occurrence_end = occurrence + timedelta(minutes=duration)
            if not event.duration_minutes:
                occurrence_end = min(occurrence_end, occurrence.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1))
            for day in days:
                day_start = day['date']
                day_end = day_start + timedelta(days=1)
                if occurrence < day_end and occurrence_end > day_start:
                    segment_start = max(occurrence, day_start)
                    segment_end = min(occurrence_end, day_end)
                    day['timed'].append(dict(row,
                        start_minute=(segment_start - day_start).total_seconds() / 60,
                        end_minute=(segment_end - day_start).total_seconds() / 60,
                        continues_before=occurrence < day_start,
                        continues_after=occurrence_end > day_end))
    for day in days:
        sort_calendar_occurrences(day['all_day'], sort_order)
        _layout_week_events(day['timed'])
    return days


def sort_calendar_occurrences(occurrences, sort_order):
    """Keep days chronological, sorting events within each day with stable ties."""
    if sort_order not in CALENDAR_SORT_OPTIONS:
        sort_order = DEFAULT_CALENDAR_SORT
    field, direction = sort_order.rsplit('_', 1)
    attribute = {'created': 'created_at', 'modified': 'modified_at',
                 'start': 'start_at', 'title': 'title'}[field]

    def value(row):
        result = getattr(row['event'], attribute)
        return (result or '').casefold() if field == 'title' else result or datetime.min

    occurrences.sort(key=lambda row: (row['event'].id, row['occurrence_at']))
    occurrences.sort(key=value, reverse=direction == 'desc')
    # Missing timestamps stay last in either direction.
    occurrences.sort(key=lambda row: getattr(row['event'], attribute) is None)
    occurrences.sort(key=lambda row: row['occurrence_at'].date())


def normalize_calendar_rule(value):
    if value is None:
        return ''
    if not isinstance(value, str):
        raise ValueError('Invalid calendar recurrence: enter a recurrence rule or leave it blank.')
    rule = value.strip()
    if rule.upper().startswith('RRULE:'):
        rule = rule.split(':', 1)[1].strip()
        if not rule:
            raise ValueError('Invalid calendar recurrence: enter a rule after RRULE:.')
    return rule


def _calendar_rule(value, start_at):
    text = normalize_calendar_rule(value)
    if not text:
        return text, None
    try:
        if not isinstance(start_at, datetime):
            raise ValueError('Enter a valid event start date.')
        if rrulestr is None:
            raise ValueError('Calendar recurrence support is unavailable.')
        parts = {}
        for part in text.upper().split(';'):
            name, value = part.split('=', 1)
            if name in parts:
                raise ValueError('Each recurrence field may appear only once.')
            parts[name] = value
        for name in ('INTERVAL', 'COUNT'):
            if name in parts and int(parts[name]) <= 0:
                raise ValueError(f'{name} must be a positive integer.')
        ranges = {'BYMONTH': (1, 12), 'BYMONTHDAY': (-31, 31), 'BYYEARDAY': (-366, 366),
                  'BYWEEKNO': (-53, 53), 'BYSETPOS': (-366, 366), 'BYHOUR': (0, 23),
                  'BYMINUTE': (0, 59), 'BYSECOND': (0, 59)}
        for name, (low, high) in ranges.items():
            if name in parts:
                values = [int(value) for value in parts[name].split(',')]
                if any(not low <= value <= high or (low < 0 and value == 0) for value in values):
                    raise ValueError(f'{name} contains an unsupported value.')
        if 'BYDAY' in parts:
            for value in parts['BYDAY'].split(','):
                match = re.fullmatch(r'([+-]?\d+)?(MO|TU|WE|TH|FR|SA|SU)', value)
                if not match or (match[1] and not 1 <= abs(int(match[1])) <= 53):
                    raise ValueError('BYDAY contains an unsupported weekday.')
        # Dates in this app are calendar dates, stored without a timezone.
        rule = rrulestr(text, dtstart=start_at.replace(tzinfo=None))
        return text, rule
    except (ValueError, TypeError, KeyError, OverflowError) as error:
        raise ValueError(f'Invalid calendar recurrence: {error}') from None


def validate_calendar_recurrence(value, start_at):
    text, _ = _calendar_rule(value, start_at)
    return text or None


def validate_calendar_imports(db, items):
    """Validate the final rule and start date after all partial updates."""
    merged = {}
    for index, item in enumerate(items):
        identifier = item.get('id')
        existing = db.get(CalendarEvent, identifier) if identifier is not None else None
        key = existing.id if existing else identifier if identifier is not None else ('new', index)
        values = merged.setdefault(key, {'start_at': existing.start_at if existing else None,
                                         'rrule': existing.rrule if existing else None,
                                         'all_day': existing.all_day if existing else True,
                                         'duration_minutes': existing.duration_minutes if existing else None})
        for field in ('start_at', 'rrule', 'all_day', 'duration_minutes'):
            if field in item:
                values[field] = item[field]
    for identifier, values in merged.items():
        try:
            start = values['start_at']
            if isinstance(start, str):
                start = datetime.fromisoformat(start)
            validate_calendar_recurrence(values['rrule'], start)
            if not isinstance(values['all_day'], bool):
                raise ValueError('All-day must be true or false.')
            parse_calendar_duration(values['duration_minutes'])
        except (ValueError, TypeError) as error:
            raise ValueError(f'Import failed for calendar_events #{identifier}: {error}') from None


def calendar_occurrences(event, start, end, limit, *, strict=False):
    if not event.rrule:
        return [event.start_at] if start <= event.start_at <= end else []
    if rrulestr is None:
        if strict:
            raise ValueError('Calendar recurrence support is unavailable.')
        return [event.start_at] if start <= event.start_at <= end else []
    try:
        _, rule = _calendar_rule(event.rrule, event.start_at)
        if rule is None:
            return [event.start_at] if start <= event.start_at <= end else []
        occurrences = []
        for occurrence in rule.xafter(start, count=limit, inc=True):
            if occurrence > end:
                break
            occurrences.append(occurrence)
        return occurrences
    except (ValueError, TypeError, KeyError, OverflowError, IndexError) as error:
        logger.warning('Calendar event #%s has an invalid recurrence: %s', event.id, error)
        if strict:
            raise ValueError(f'Calendar event #{event.id} has an invalid recurrence.') from error
        return []
