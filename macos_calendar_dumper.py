#!/usr/bin/env python3
"""
macos_calendar_dumper - list macOS Calendar calendars and events, optionally export to CSV or JSON.

Requires:  pip install pyobjc-framework-EventKit
On first run macOS asks for Calendar access for the app running this script
(Terminal, iTerm, VS Code, ...). If you missed the prompt, allow it under
System Settings > Privacy & Security > Calendars.

Usage
-----
  python macos_calendar_dumper.py                    interactive menu
  python macos_calendar_dumper.py calendars          list calendars (numbered)
  python macos_calendar_dumper.py events -c Work     events in "Work", current month
  python macos_calendar_dumper.py events -c Work -f 01012026
  python macos_calendar_dumper.py events -c Work -f 01092026 -t 30092026
  python macos_calendar_dumper.py events -c Work -c 3 -s dentist -f 01012026 -o out.csv
  python macos_calendar_dumper.py events -c ALL -f 01092026 -t 30092026 -o sept.csv
  python macos_calendar_dumper.py events -c ALL -f 01092026 -o sept.json
  python macos_calendar_dumper.py events -c Work --format json      (auto file name)

  -c/--calendar  calendar name (case-insensitive) or its number from
                 "calendars"; repeat to include several calendars;
                 ALL (any case) selects every calendar
  -f/--from      start date DDMMYYYY
  -t/--to        end date DDMMYYYY (inclusive); default today
  -s/--search    text to find in title, location or notes (case-insensitive)
  -o/--output    write the results to this file
  --format       csv or json. If omitted, taken from the -o extension
                 (.json -> json, anything else -> csv). If given without -o,
                 the file is named events_<from>_<to>.<format>.

With no dates, the current month is used. With only a start date, the range
runs to the end of today.

  -h/--help      show help and exit
  -V/--version   show the version and exit
-h and -V are standalone: when present, every other argument is ignored and
whichever of the two comes first wins.
"""

import argparse
import csv
import json
import os
import sys
import time
from datetime import date, datetime, timedelta

__version__ = "0.0.1"   # version of this script
current_script_name = os.path.basename(__file__)  # shown in usage, help and error messages

DATE_INPUT_FORMAT = "%d%m%Y"   # how dates are typed on the command line: DDMMYYYY
EXPORT_FORMATS = ("csv", "json")
CSV_COLUMNS = ["calendar", "title", "start", "end", "all_day", "location", "notes", "url"]


# --------------------------------------------------------------------------- #
# Date handling
# --------------------------------------------------------------------------- #

def parse_date(text_representation_of_date):
    try:
        return datetime.strptime(text_representation_of_date.strip(), DATE_INPUT_FORMAT)
    except ValueError:
        raise ValueError(
            f"Invalid date '{text_representation_of_date}'. Use DDMMYYYY, e.g. 01092026."
        ) from None


def resolve_range(start_date_text=None, end_date_text=None, today=None):
    """Return (start_date, end_date_exclusive) datetimes for the requested range.

    end_date_exclusive is the first moment AFTER the range: -t 30092026 means
    "up to and including 30 September", which becomes 01.10.2026 00:00. Events
    must start before that point, so all of 30 September is covered.
    """
    today = today or date.today()
    start_date_text = (start_date_text or "").strip()
    end_date_text = (end_date_text or "").strip()

    if end_date_text and not start_date_text:
        raise ValueError("An end date was given without a start date.")

    if not start_date_text:  # use the current month
        start_date = datetime(today.year, today.month, 1)
        end_date_exclusive = datetime(today.year + (today.month == 12), today.month % 12 + 1, 1)
    else:
        start_date = parse_date(start_date_text)
        if end_date_text:
            last_day = parse_date(end_date_text)
        else:
            last_day = datetime(today.year, today.month, today.day)
        end_date_exclusive = last_day + timedelta(days=1)  # the end date is inclusive

    # The end date must not be before the start date.
    if end_date_exclusive <= start_date:
        if not end_date_text:
            raise ValueError(
                f"Start date {start_date:%d.%m.%Y} is in the future. Without -t the range "
                f"ends today; give an end date with -t to look ahead."
            )
        raise ValueError(
            f"End date {(end_date_exclusive - timedelta(days=1)):%d.%m.%Y} is before "
            f"start date {start_date:%d.%m.%Y}."
        )
    return start_date, end_date_exclusive


# --------------------------------------------------------------------------- #
# EventKit access
# --------------------------------------------------------------------------- #

def open_calendar_store(timeout=60):
    """Return (calendar_store, EventKit module) once Calendar access is granted.

    Checks the current permission first, so an earlier "Don't Allow" fails
    straight away instead of waiting for a prompt macOS will never show.
    """
    try:
        import EventKit
    except ImportError:
        sys.exit("EventKit bindings missing. Install them with:\n  pip install -r requirements.txt")

    # EventKit's numeric constants, used directly so Pylance has nothing to flag.
    ENTITY_EVENT = 0     # EKEntityTypeEvent
    RESTRICTED = 1       # EKAuthorizationStatusRestricted
    DENIED = 2           # EKAuthorizationStatusDenied
    FULL_ACCESS = 3      # EKAuthorizationStatusFullAccess ("Authorized" before macOS 14)
    WRITE_ONLY = 4       # EKAuthorizationStatusWriteOnly (macOS 14+)

    EKEventStore = EventKit.EKEventStore  # pyright: ignore[reportAttributeAccessIssue]
    settings_hint = (
        "Allow it under System Settings > Privacy & Security > Calendars for the app "
        "running this script (Terminal, VS Code, ...), quit and reopen that app, "
        "then try again."
    )

    calendar_store_access_authorisation_status = EKEventStore.authorizationStatusForEntityType_(
        ENTITY_EVENT
    )
    if calendar_store_access_authorisation_status == DENIED:
        sys.exit("Calendar access was denied earlier. " + settings_hint)
    if calendar_store_access_authorisation_status == RESTRICTED:
        sys.exit("Calendar access is blocked on this Mac (e.g. by Screen Time or a "
                 "device-management profile) and can't be granted from here.")

    calendar_store = EKEventStore.alloc().init()
    if calendar_store_access_authorisation_status == FULL_ACCESS:
        return calendar_store, EventKit

    # Not asked yet, or write-only access (can add events but not read them): ask now.
    if calendar_store_access_authorisation_status == WRITE_ONLY:
        print("This app can add calendar events but not read them.", file=sys.stderr)
    print("Waiting for you to answer the macOS Calendar access prompt...", file=sys.stderr)

    result = {}

    def calendar_store_access(granted, error):
        result["granted"] = bool(granted)

    if calendar_store.respondsToSelector_("requestFullAccessToEventsWithCompletion:"):  # macOS 14+
        calendar_store.requestFullAccessToEventsWithCompletion_(calendar_store_access)
    else:
        calendar_store.requestAccessToEntityType_completion_(
            ENTITY_EVENT, calendar_store_access
        )

    deadline = time.time() + timeout
    while "granted" not in result and time.time() < deadline:
        time.sleep(0.1)

    if "granted" not in result:
        sys.exit(f"No answer to the Calendar access prompt within {timeout} seconds. "
                 "If no prompt appeared (e.g. over SSH), run the script from Terminal "
                 "on the Mac itself.")
    if not result["granted"]:
        sys.exit("Calendar access was not granted. " + settings_hint)
    return calendar_store, EventKit


def get_calendars(calendar_store, EventKit):
    """All event calendars, sorted by name, as dicts with a 1-based 'number'."""
    calendars = []
    for calendar in calendar_store.calendarsForEntityType_(EventKit.EKEntityTypeEvent) or []:
        source = calendar.source()
        calendars.append({
            "title": str(calendar.title()),
            "account": str(source.title()) if source is not None else "",
            "eventkit_calendar": calendar,
        })
    calendars.sort(key=lambda calendar: (calendar["title"].lower(), calendar["account"].lower()))
    for number, calendar in enumerate(calendars, 1):
        calendar["number"] = number
    return calendars


def select_calendars(all_calendars, requested_calendars):
    """Resolve names or numbers to calendars. Raises ValueError if any is unknown.

    The keyword ALL (any case) selects every calendar.
    """
    for name_or_number in requested_calendars:
        if name_or_number.strip().lower() == "all":
            return list(all_calendars)

    selected_calendars, unknown = [], []
    for name_or_number in requested_calendars:
        name_or_number = name_or_number.strip()
        if name_or_number.isdigit() and 1 <= int(name_or_number) <= len(all_calendars):
            matches = [all_calendars[int(name_or_number) - 1]]
        else:
            matches = [
                calendar for calendar in all_calendars
                if calendar["title"].lower() == name_or_number.lower()
            ]
        if not matches:
            unknown.append(name_or_number)
        for matched_calendar in matches:
            if matched_calendar not in selected_calendars:
                selected_calendars.append(matched_calendar)
    if unknown:
        raise ValueError(
            f"Unknown calendar(s): {', '.join(unknown)}. "
            f"Run 'python {current_script_name} calendars' to see the list."
        )
    return selected_calendars


# --------------------------------------------------------------------------- #
# Events
# --------------------------------------------------------------------------- #

def _text_or_empty(value):
    """Return the string representation of value, or an empty string if value is None."""
    return str(value) if value is not None else ""


def _format_event_date(nsdate, all_day):
    """Return an event date as text: date only for all-day events, else date and time."""
    event_datetime = datetime.fromtimestamp(nsdate.timeIntervalSince1970())
    return event_datetime.strftime("%Y-%m-%d") if all_day else event_datetime.strftime("%Y-%m-%d %H:%M")


def fetch_events(calendar_store, selected_calendars, start_date, end_date_exclusive, search_text=None):
    """Events overlapping [start_date, end_date_exclusive) in the given calendars, sorted by start."""
    from Foundation import NSDate  # pyright: ignore[reportAttributeAccessIssue]

    def python_datetime_to_nsdate(python_datetime):
        """Convert a Python datetime into an Apple NSDate."""
        return NSDate.dateWithTimeIntervalSince1970_(python_datetime.timestamp())

    eventkit_calendars = [calendar["eventkit_calendar"] for calendar in selected_calendars]
    search_text_lowercase = search_text.lower() if search_text else None
    seen_occurrences, events = set(), []

    # EventKit caps a single query at about 4 years, so query one year at a time.
    chunk_start = start_date
    while chunk_start < end_date_exclusive:
        chunk_end = min(chunk_start + timedelta(days=365), end_date_exclusive)
        predicate = calendar_store.predicateForEventsWithStartDate_endDate_calendars_(
            python_datetime_to_nsdate(chunk_start),
            python_datetime_to_nsdate(chunk_end),
            eventkit_calendars,
        )
        for event in calendar_store.eventsMatchingPredicate_(predicate) or []:
            occurrence_key = (
                _text_or_empty(event.eventIdentifier()),
                event.startDate().timeIntervalSince1970(),
            )
            if occurrence_key in seen_occurrences:  # events spanning a chunk boundary come back twice
                continue
            seen_occurrences.add(occurrence_key)

            title = _text_or_empty(event.title())
            location = _text_or_empty(event.location())
            notes = _text_or_empty(event.notes())
            if search_text_lowercase and not any(
                search_text_lowercase in field_text.lower() for field_text in (title, location, notes)
            ):
                continue

            all_day = bool(event.isAllDay())
            event_url = event.URL()
            events.append({
                "calendar": _text_or_empty(event.calendar().title()),
                "title": title,
                "start": _format_event_date(event.startDate(), all_day),
                "end": _format_event_date(event.endDate(), all_day),
                "all_day": "yes" if all_day else "no",
                "location": location,
                "notes": notes,
                "url": _text_or_empty(event_url.absoluteString()) if event_url is not None else "",
                "_sort": event.startDate().timeIntervalSince1970(),
            })
        chunk_start = chunk_end

    events.sort(key=lambda event: (event["_sort"], event["title"].lower()))
    for event in events:
        del event["_sort"]
    return events


# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #

def print_calendars(calendars):
    if not calendars:
        print("No calendars found.")
        return
    title_width = max(len(calendar["title"]) for calendar in calendars)
    for calendar in calendars:
        print(f"{calendar['number']:>3}  {calendar['title']:<{title_width}}  [{calendar['account']}]")


def print_events(events, start_date, end_date_exclusive):
    last_date = end_date_exclusive - timedelta(days=1)
    print(f"\n{len(events)} event(s) from {start_date:%d.%m.%Y} to {last_date:%d.%m.%Y}\n")
    if not events:
        return
    calendar_column_width = min(max(len(event["calendar"]) for event in events), 20)
    for event in events:
        if event["all_day"] == "yes":
            # A multi-day all-day event shows its last day too.
            if event["start"] == event["end"]:
                when = event["start"]
            else:
                when = f"{event['start']} - {event['end']}"
        elif event["start"][:10] == event["end"][:10]:
            when = f"{event['start']} - {event['end'][-5:]}"   # same day: end time only
        else:
            when = f"{event['start']} - {event['end']}"
        location_suffix = f"  @ {event['location']}" if event["location"] else ""
        calendar_name = event["calendar"][:calendar_column_width]
        print(f"{when:<35}  {calendar_name:<{calendar_column_width}}  {event['title']}{location_suffix}")


def write_csv(events, output_path):
    with open(output_path, "w", newline="", encoding="utf-8") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(events)


def write_json(events, output_path):
    """A JSON array of event objects; all_day becomes a real true/false."""
    json_events = [{**event, "all_day": event["all_day"] == "yes"} for event in events]
    with open(output_path, "w", encoding="utf-8") as output_file:
        json.dump(json_events, output_file, ensure_ascii=False, indent=2)
        output_file.write("\n")


def resolve_output(output_path, export_format, start_date, end_date_exclusive):
    """Work out (output_path, export_format) from -o and --format. Returns (None, None) for no export."""
    if not output_path and not export_format:
        return None, None
    if not export_format:
        export_format = "json" if output_path.lower().endswith(".json") else "csv"
    if not output_path:
        last_date = end_date_exclusive - timedelta(days=1)
        output_path = f"events_{start_date:%d%m%Y}_{last_date:%d%m%Y}"
    if not os.path.splitext(output_path)[1]:
        output_path += "." + export_format
    return output_path, export_format


def export(events, output_path, export_format):
    """Write the events to a file. Returns True on success, False (with a message) on failure."""
    writer = write_json if export_format == "json" else write_csv
    try:
        writer(events, output_path)
    except OSError as error:
        print(f"\nError: could not write {output_path}: {error.strerror or error}", file=sys.stderr)
        return False
    print(f"\nSaved {len(events)} event(s) to {output_path} ({export_format.upper()})")
    return True


# --------------------------------------------------------------------------- #
# Command line
# --------------------------------------------------------------------------- #

HELP_FLAGS = ("-h", "--help")
VERSION_FLAGS = ("-V", "--version")


def usage(file=None):
    """Print the short usage message (to stdout unless another file is given)."""
    file = file or sys.stdout
    continuation_indent = " " * (len(current_script_name) + 10)
    print(
        "Usage:\n"
        f"  {current_script_name}                    interactive menu\n"
        f"  {current_script_name} calendars\n"
        f"  {current_script_name} events -c CALENDAR [-c CALENDAR ...] [-f DDMMYYYY [-t DDMMYYYY]]\n"
        f"{continuation_indent}[-s TEXT] [-o FILE] [--format csv|json]\n"
        f"  {current_script_name} -h | --help\n"
        f"  {current_script_name} -V | --version",
        file=file,
    )


def print_help():
    """Print usage plus a description of every command and option."""
    print(f"{current_script_name} {__version__} - list macOS Calendar calendars and events, "
          f"optionally export to CSV or JSON.\n")
    usage()
    print(f"""
Commands:
  (none)              interactive menu
  calendars           list all calendars, numbered
  events              list events in one or more calendars

Options for 'events':
  -c, --calendar CAL  calendar name (case-insensitive) or its number from
                      'calendars'; repeat for several, or ALL for every calendar
  -f, --from DATE     start date DDMMYYYY
  -t, --to DATE       end date DDMMYYYY, inclusive (default: today)
  -s, --search TEXT   only events whose title, location or notes contain TEXT
                      (case-insensitive)
  -o, --output FILE   export the results to FILE
      --format FMT    csv or json; default taken from the -o extension
                      (.json -> json, else csv). Without -o the file is named
                      events_<from>_<to>.<format>

Dates:
  no dates            current month
  -f only             from that date to the end of today
  -f and -t           that range, both days included

Standalone options:
  -h, --help          show this help and exit
  -V, --version       show the version and exit
  If either appears, all other arguments are ignored; the first one wins.

Examples:
  {current_script_name} events -c Work -f 01092026 -t 30092026
  {current_script_name} events -c ALL -f 01012026 -s dentist -o dentist.json
""".rstrip())


def print_version():
    print(f"{current_script_name} {__version__}")


def standalone_flag(command_line_arguments):
    """Return the first -h/--help or -V/--version in the arguments, or None.

    Arguments after a bare '--' are treated as values, not options.
    """
    for argument in command_line_arguments:
        if argument == "--":
            return None
        if argument in HELP_FLAGS or argument in VERSION_FLAGS:
            return argument
    return None


class Parser(argparse.ArgumentParser):
    """argparse with our own usage text on errors."""

    def error(self, message):
        usage(file=sys.stderr)
        print(f"\nError: {message}\nRun '{current_script_name} -h' for help.", file=sys.stderr)
        sys.exit(2)


def run_cli(arguments):
    """Run the command-line interface with the given parsed arguments."""
    calendar_store, EventKit = open_calendar_store()
    all_calendars = get_calendars(calendar_store, EventKit)

    if arguments.command == "calendars":
        print_calendars(all_calendars)
        return

    try:
        selected_calendars = select_calendars(all_calendars, arguments.calendar)
        start_date, end_date_exclusive = resolve_range(arguments.date_from, arguments.date_to)
    except ValueError as error:
        sys.exit(f"Error: {error}")

    events = fetch_events(
        calendar_store, selected_calendars, start_date, end_date_exclusive, arguments.search
    )
    print_events(events, start_date, end_date_exclusive)

    output_path, export_format = resolve_output(
        arguments.output, arguments.format, start_date, end_date_exclusive
    )
    if output_path and not export(events, output_path, export_format):
        sys.exit(1)


def build_parser():
    # -h/-V are handled before parsing (see main), so argparse's own help is off.
    parser = Parser(add_help=False)
    commands = parser.add_subparsers(dest="command", parser_class=Parser)
    commands.add_parser("calendars", add_help=False)

    events_parser = commands.add_parser("events", add_help=False)
    events_parser.add_argument("-c", "--calendar", action="append", required=True,
                               help="calendar name or number (repeat for several), or ALL for every calendar")
    events_parser.add_argument("-f", "--from", dest="date_from", metavar="DDMMYYYY", help="start date")
    events_parser.add_argument("-t", "--to", dest="date_to", metavar="DDMMYYYY",
                               help="end date, inclusive (default: today)")
    events_parser.add_argument("-s", "--search", help="text to find in title, location or notes")
    events_parser.add_argument("-o", "--output", metavar="FILE", help="export results to this file")
    events_parser.add_argument("--format", choices=EXPORT_FORMATS, type=str.lower,
                               help="export format: csv or json (default: from the -o extension, else csv)")
    return parser


# --------------------------------------------------------------------------- #
# Interactive menu
# --------------------------------------------------------------------------- #

def ask_calendars(all_calendars):
    """Ask the user to select one or more calendars, returning the selected list."""
    print_calendars(all_calendars)
    while True:
        answer = input("\nCalendar number(s) or name(s), comma-separated, or ALL: ").strip()
        if not answer:
            continue
        try:
            return select_calendars(all_calendars, answer.split(","))
        except ValueError as error:
            print(error)


def ask_range():
    """Ask the user for a start and end date, returning (start_date, end_date_exclusive)."""
    while True:
        start_date_text = input("Start date DDMMYYYY (blank = current month): ").strip()
        end_date_text = input("End date DDMMYYYY (blank = today): ").strip() if start_date_text else ""
        try:
            return resolve_range(start_date_text, end_date_text)
        except ValueError as error:
            print(error)


def run_menu():
    """Run the interactive menu."""
    calendar_store, EventKit = open_calendar_store()
    all_calendars = get_calendars(calendar_store, EventKit)

    while True:
        print("\n")
        print("1) List calendars")
        print("2) List events in a calendar (date range)")
        print("3) List events in a calendar (text + date range)")
        print("q) Quit")
        choice = input("> ").strip().lower()

        if choice == "1":
            print()
            print_calendars(all_calendars)
        elif choice in ("2", "3"):
            selected_calendars = ask_calendars(all_calendars)
            search_text = None
            if choice == "3":
                while not search_text:
                    search_text = input("Search text (title, location, notes): ").strip()
            start_date, end_date_exclusive = ask_range()
            events = fetch_events(
                calendar_store, selected_calendars, start_date, end_date_exclusive, search_text
            )
            print_events(events, start_date, end_date_exclusive)
            if events:
                export_format = ""
                while export_format not in EXPORT_FORMATS:
                    export_format = input("\nExport? csv / json (blank = skip): ").strip().lower()
                    if not export_format:
                        break
                if export_format:
                    file_name = input("File name (blank = automatic): ").strip()
                    output_path, export_format = resolve_output(
                        file_name, export_format, start_date, end_date_exclusive
                    )
                    export(events, output_path, export_format)
        elif choice in ("q", "quit", "exit"):
            return
        else:
            print("Please choose 1, 2, 3 or q.")


def main(command_line_arguments=None):
    if command_line_arguments is None:
        command_line_arguments = sys.argv[1:]

    flag = standalone_flag(command_line_arguments)
    if flag in HELP_FLAGS:
        print_help()
        return
    if flag in VERSION_FLAGS:
        print_version()
        return

    arguments = build_parser().parse_args(command_line_arguments)
    try:
        if arguments.command is None:
            run_menu()
        else:
            run_cli(arguments)
    except KeyboardInterrupt:  # Ctrl+C
        print()
        sys.exit(130)  # the usual exit code for "stopped with Ctrl+C"
    except EOFError:  # Ctrl+D at a menu prompt: treat as quit
        print()


if __name__ == "__main__":
    main()
