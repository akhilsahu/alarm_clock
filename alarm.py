"""Core alarm clock module with deterministic time injection and interruptible waits."""

import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional


def parse_duration(s: str) -> float:
    """Parses duration strings like '10s', '5m', '1.5h', or raw seconds '30'.
    
    Raises ValueError for invalid, negative, or malformed duration strings.
    """
    s = s.strip().lower()
    if not s:
        raise ValueError("Duration cannot be empty.")

    multiplier = 1.0
    if s.endswith("s"):
        num_part = s[:-1]
    elif s.endswith("m"):
        num_part = s[:-1]
        multiplier = 60.0
    elif s.endswith("h"):
        num_part = s[:-1]
        multiplier = 3600.0
    else:
        num_part = s

    try:
        val = float(num_part)
    except ValueError as e:
        raise ValueError(f"Invalid duration format: '{s}'") from e

    if val < 0:
        raise ValueError("Duration cannot be negative.")

    return val * multiplier


def format_seconds(seconds: float) -> str:
    """Formats seconds into human-readable duration like '1h 05m 12s' or '45s'."""
    sec = max(0, int(seconds))
    hrs = sec // 3600
    sec %= 3600
    mins = sec // 60
    sec %= 60

    if hrs > 0:
        return f"{hrs}h {mins:02d}m {sec:02d}s"
    if mins > 0:
        return f"{mins}m {sec:02d}s"
    return f"{sec}s"


@dataclass
class Alarm:
    id: int
    target_epoch: float
    label: str
    cancel_event: threading.Event = field(default_factory=threading.Event)

    @property
    def is_cancelled(self) -> bool:
        return self.cancel_event.is_set()


class AlarmManager:
    """Thread-safe alarm manager with injectable time and interruptible waits."""

    def __init__(
        self,
        time_fn: Callable[[], float] = time.time,
        on_trigger: Optional[Callable[[Alarm], None]] = None,
        tick_interval: float = 1.0,
    ):
        self.time_fn = time_fn
        self.on_trigger = on_trigger or self._default_trigger
        self.tick_interval = tick_interval

        self.active_alarms: dict[int, Alarm] = {}
        self.firing_alarms: dict[int, Alarm] = {}
        self.lock = threading.Lock()

        self._next_id = 1
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._worker_thread: Optional[threading.Thread] = None

    def _default_trigger(self, alarm: Alarm) -> None:
        """Default trigger: visual alert + ASCII bell."""
        print(f"\n\a🔔 [ALARM #{alarm.id}] {alarm.label}! 🔔\nalarm> ", end="", flush=True)

    def set_alarm(self, delay_seconds: float, label: str = "Alarm") -> int:
        """Schedules a new alarm. Wakes up the worker loop to recompute delay."""
        if delay_seconds < 0:
            raise ValueError("Delay cannot be negative.")

        with self.lock:
            alarm_id = self._next_id
            self._next_id += 1
            now = self.time_fn()
            alarm = Alarm(
                id=alarm_id,
                target_epoch=now + delay_seconds,
                label=label,
            )
            self.active_alarms[alarm_id] = alarm

        # Wake worker so it recalculates its sleep time for an earlier alarm
        self._wake_event.set()
        return alarm_id

    def cancel_alarm(self, alarm_id: int) -> bool:
        """Cancels an alarm cleanly.
        
        Handles both active alarms and in-flight alarms currently transitioning to trigger.
        Wakes the worker immediately to adjust remaining wait time.
        """
        with self.lock:
            # 1. Active alarms awaiting trigger
            if alarm_id in self.active_alarms:
                alarm = self.active_alarms.pop(alarm_id)
                alarm.cancel_event.set()
                self._wake_event.set()
                return True

            # 2. In-flight alarms (transitioned, about to fire outside lock)
            if alarm_id in self.firing_alarms:
                alarm = self.firing_alarms[alarm_id]
                alarm.cancel_event.set()
                return True

            return False

    def tick(self) -> list[Alarm]:
        """Processes due alarms synchronously for the current moment.
        
        Returns the list of alarms that successfully fired.
        Designed for deterministic invocation in tests or from the worker loop.
        """
        now = self.time_fn()
        due: list[Alarm] = []

        with self.lock:
            for alarm_id, alarm in list(self.active_alarms.items()):
                if now >= alarm.target_epoch:
                    self.active_alarms.pop(alarm_id)
                    self.firing_alarms[alarm_id] = alarm
                    due.append(alarm)

        triggered: list[Alarm] = []
        for alarm in due:
            with self.lock:
                was_cancelled = alarm.is_cancelled
                self.firing_alarms.pop(alarm.id, None)

            # Fire outside the lock so I/O does not block set/cancel operations
            if not was_cancelled:
                self.on_trigger(alarm)
                triggered.append(alarm)

        return triggered

    def get_active_alarms(self) -> list[Alarm]:
        """Returns a snapshot of active alarms sorted by earliest target time."""
        with self.lock:
            return sorted(self.active_alarms.values(), key=lambda a: a.target_epoch)

    def start(self) -> None:
        """Starts the background worker thread."""
        self._stop_event.clear()
        self._wake_event.clear()
        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker_thread.start()

    def stop(self) -> None:
        """Stops the worker thread cleanly and wakes it up immediately."""
        self._stop_event.set()
        self._wake_event.set()
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=2.0)

    def _worker_loop(self) -> None:
        """Continuous background loop using interruptible waits."""
        while not self._stop_event.is_set():
            self.tick()

            # Determine sleep time based on next due alarm or tick interval
            with self.lock:
                if self.active_alarms:
                    earliest = min(a.target_epoch for a in self.active_alarms.values())
                    delay = max(0.0, earliest - self.time_fn())
                    sleep_time = min(delay, self.tick_interval)
                else:
                    sleep_time = self.tick_interval

            # Interruptible wait: wakes immediately on wake_event or stop_event
            self._wake_event.wait(timeout=sleep_time)
            self._wake_event.clear()

