"""Interactive CLI interface for the alarm clock."""

import sys
import time
from alarm import AlarmManager, format_seconds, parse_duration


def print_help():
    print("""
Commands:
  set <duration> [label]   Schedule alarm (e.g. 'set 10s', 'set 5m Tea', 'set 1h')
  cancel <id>              Cancel active alarm by numeric ID
  list                     List all active alarms and remaining ETAs
  help                     Show this help message
  exit / quit              Exit the program
""")


def main():
    manager = AlarmManager()
    manager.start()

    print("=" * 55)
    print(" ⏰ Python CLI Alarm Clock")
    print(" Type 'help' for commands, 'exit' or Ctrl+C to quit.")
    print("=" * 55)

    try:
        while True:
            try:
                raw = input("alarm> ").strip()
            except EOFError:
                break

            if not raw:
                continue

            parts = raw.split(maxsplit=2)
            cmd = parts[0].lower()

            if cmd == "set":
                if len(parts) < 2:
                    print("Usage: set <duration> [label]  (e.g. 'set 30s', 'set 10m Meeting')")
                    continue
                try:
                    duration = parse_duration(parts[1])
                    label = parts[2] if len(parts) > 2 else "Reminder"
                    alarm_id = manager.set_alarm(duration, label)
                    print(f"✓ Alarm #{alarm_id} scheduled for {format_seconds(duration)} from now ('{label}')")
                except ValueError as err:
                    print(f"Error: {err}")

            elif cmd == "cancel":
                if len(parts) < 2:
                    print("Usage: cancel <id>  (e.g. 'cancel 1')")
                    continue
                try:
                    alarm_id = int(parts[1])
                    if manager.cancel_alarm(alarm_id):
                        print(f"✓ Alarm #{alarm_id} cancelled.")
                    else:
                        print(f"Error: Alarm #{alarm_id} not found or already fired.")
                except ValueError:
                    print("Error: Alarm ID must be an integer.")

            elif cmd == "list":
                alarms = manager.get_active_alarms()
                if not alarms:
                    print("No active alarms.")
                else:
                    now = time.time()
                    print(f"Active alarms ({len(alarms)}):")
                    for a in alarms:
                        remaining = max(0.0, a.target_epoch - now)
                        print(f"  #{a.id} [{a.label}]: {format_seconds(remaining)} remaining")

            elif cmd == "help":
                print_help()

            elif cmd in ("exit", "quit"):
                break

            else:
                print(f"Unknown command: '{cmd}'. Type 'help' for available commands.")

    except KeyboardInterrupt:
        print("\nInterrupted.")
    finally:
        print("Shutting down worker...")
        manager.stop()
        print("Goodbye!")


if __name__ == "__main__":
    main()

