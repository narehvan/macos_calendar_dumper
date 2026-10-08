# macos_calendar_dumper.py

List the calendars and events from the macOS Calendar app, filter them by date
range and text, and export them to CSV or JSON.

It reads every account synced to your Mac (iCloud, Google, Exchange, ...)
through Apple's EventKit framework, the same one the Calendar app uses.
Recurring events are expanded into their individual occurrences.

## Requirements

- macOS
- Python 3.9 or later
- [PyObjC](https://pyobjc.readthedocs.io/) EventKit bindings (installation steps below)

## Installation

```bash
# 1. Put the project files in a folder and go there
cd macos_calendar_dumper

# 2. (Recommended) create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 3. Install the dependency
pip install -r requirements.txt
```

### Calendar permission

The first time you run the script, macOS asks whether the app running it
(Terminal, iTerm, VS Code, ...) may access your calendars. Click **Allow**.

If you clicked **Don't Allow**, or no prompt appeared:

1. Open **System Settings > Privacy & Security > Calendars**.
2. Turn on access (full access) for the app you run the script from.
3. Quit and reopen that app, then run the script again.

## Usage

```text
python macos_calendar_dumper.py                     interactive menu
python macos_calendar_dumper.py calendars           list calendars
python macos_calendar_dumper.py events -c CALENDAR [-c CALENDAR ...]
                                [-f DDMMYYYY [-t DDMMYYYY]]
                                [-s TEXT] [-o FILE] [--format csv|json]
python macos_calendar_dumper.py -h | --help         show help
python macos_calendar_dumper.py -V | --version      show version
```

### Interactive menu

Run the script without arguments to get a menu:

```text
1) List calendars
2) List events in a calendar (date range)
3) List events in a calendar (text + date range)
q) Quit
```

After the events are listed, the menu offers to export them to CSV or JSON.

### List calendars

```bash
python macos_calendar_dumper.py calendars
```

```text
  1  Birthdays  [Other]
  2  Home       [iCloud]
  3  Work       [Exchange]
```

The number in front of each calendar can be used with `-c` instead of its name.

### List events

| Option | Description |
|---|---|
| `-c`, `--calendar CAL` | Calendar name (case-insensitive) or its number from `calendars`. Repeat for several calendars. `ALL` selects every calendar. **Required.** |
| `-f`, `--from DDMMYYYY` | Start date. |
| `-t`, `--to DDMMYYYY` | End date, inclusive. Needs `-f`. |
| `-s`, `--search TEXT` | Only events whose title, location or notes contain `TEXT` (case-insensitive). |
| `-o`, `--output FILE` | Export the results to `FILE`. |
| `--format csv\|json` | Export format. See [Export](#export). |

**Date range**

| Given | Range |
|---|---|
| no dates | the current month |
| `-f` only | from that date to the end of today |
| `-f` and `-t` | that range, both days included |

Events that start before the range but run into it are included.

### Export

- `-o sept.csv` writes CSV, `-o sept.json` writes JSON (the format follows the extension; anything other than `.json` is CSV).
- `--format` overrides the extension.
- `--format` without `-o` creates a file named `events_<from>_<to>.<format>`, e.g. `events_01092026_30092026.json`.
- A file name without an extension gets one added.

Both formats contain the same fields:

| Field | Example |
|---|---|
| `calendar` | `Work` |
| `title` | `Team meeting` |
| `start` | `2026-09-02 14:00` (all-day events: `2026-09-02`) |
| `end` | `2026-09-02 15:00` |
| `all_day` | CSV: `yes` / `no`, JSON: `true` / `false` |
| `location` | `Zürich` |
| `notes` | free text |
| `url` | `https://...` |

Files are written as UTF-8. CSV uses commas as separators.

### Examples

```bash
# Events in "Work" this month
python macos_calendar_dumper.py events -c Work

# Events in "Work" from 1 January 2026 until today
python macos_calendar_dumper.py events -c Work -f 01012026

# All calendars in September 2026, exported to CSV
python macos_calendar_dumper.py events -c ALL -f 01092026 -t 30092026 -o september.csv

# "Work" and calendar number 3, only events mentioning "dentist", as JSON
python macos_calendar_dumper.py events -c Work -c 3 -s dentist -f 01012026 -o dentist.json

# Automatic file name: events_01102026_31102026.json (when run in October 2026)
python macos_calendar_dumper.py events -c Home --format json
```

### Help and version

`-h` / `--help` and `-V` / `--version` are standalone options: when either is
present, all other arguments are ignored and whichever comes first wins.

```bash
python macos_calendar_dumper.py -h
python macos_calendar_dumper.py -V
```

## Exit codes

| Code | Meaning |
|---|---|
| `0` | success |
| `1` | an error, e.g. unknown calendar, invalid date, no calendar access, export file could not be written |
| `2` | invalid command-line arguments |
| `130` | stopped with Ctrl+C |
