"""Unit tests covering core requirements, edge cases, and concurrency."""

import threading
import time
import unittest
from alarm import Alarm, AlarmManager, format_seconds, parse_duration


class TestDurationParsing(unittest.TestCase):
    """Tests for parse_duration and format_seconds."""

    def test_valid_units(self):
        self.assertEqual(parse_duration("10s"), 10.0)
        self.assertEqual(parse_duration("5m"), 300.0)
        self.assertEqual(parse_duration("2h"), 7200.0)
        self.assertEqual(parse_duration("45"), 45.0)  # default to seconds

    def test_float_values(self):
        self.assertEqual(parse_duration("0.5s"), 0.5)
        self.assertEqual(parse_duration("1.5m"), 90.0)
        self.assertEqual(parse_duration("0.25h"), 900.0)

    def test_zero_allowed(self):
        self.assertEqual(parse_duration("0s"), 0.0)
        self.assertEqual(parse_duration("0"), 0.0)

    def test_whitespace_and_casing(self):
        self.assertEqual(parse_duration("  10S  "), 10.0)
        self.assertEqual(parse_duration("\t2M\n"), 120.0)

    def test_invalid_formats_raise_value_error(self):
        invalid_inputs = ["", "   ", "abc", "10x", "12:30", "m", "--5s"]
        for item in invalid_inputs:
            with self.subTest(item=item):
                with self.assertRaises(ValueError):
                    parse_duration(item)

    def test_negative_values_raise_value_error(self):
        for item in ["-5s", "-1m", "-0.1"]:
            with self.subTest(item=item):
                with self.assertRaises(ValueError):
                    parse_duration(item)

    def test_format_seconds(self):
        self.assertEqual(format_seconds(0), "0s")
        self.assertEqual(format_seconds(45), "45s")
        self.assertEqual(format_seconds(65), "1m 05s")
        self.assertEqual(format_seconds(3665), "1h 01m 05s")
        self.assertEqual(format_seconds(-10), "0s")  # clamps negative


class TestAlarmManagerDeterministic(unittest.TestCase):
    """Tests for AlarmManager using injected virtual time (instant & deterministic)."""

    def setUp(self):
        self.simulated_time = 1000.0
        self.triggered_alarms: list[Alarm] = []

        def fake_now() -> float:
            return self.simulated_time

        def record_trigger(alarm: Alarm) -> None:
            self.triggered_alarms.append(alarm)

        self.manager = AlarmManager(
            time_fn=fake_now,
            on_trigger=record_trigger,
            tick_interval=1.0,
        )

    def test_set_alarm_and_id_generation(self):
        id1 = self.manager.set_alarm(10.0, "First")
        id2 = self.manager.set_alarm(20.0, "Second")

        self.assertEqual(id1, 1)
        self.assertEqual(id2, 2)
        active = self.manager.get_active_alarms()
        self.assertEqual(len(active), 2)
        self.assertEqual(active[0].target_epoch, 1010.0)
        self.assertEqual(active[1].target_epoch, 1020.0)

    def test_negative_delay_rejected(self):
        with self.assertRaises(ValueError):
            self.manager.set_alarm(-5.0, "Bad")

    def test_tick_before_target_time(self):
        self.manager.set_alarm(10.0, "Wait")
        # Advance time by 9 seconds (target is 1010, now is 1009)
        self.simulated_time = 1009.0
        fired = self.manager.tick()

        self.assertEqual(len(fired), 0)
        self.assertEqual(len(self.triggered_alarms), 0)
        self.assertEqual(len(self.manager.get_active_alarms()), 1)

    def test_tick_at_and_after_target_time(self):
        self.manager.set_alarm(10.0, "Target")
        # Advance to exact target
        self.simulated_time = 1010.0
        fired = self.manager.tick()

        self.assertEqual(len(fired), 1)
        self.assertEqual(fired[0].label, "Target")
        self.assertEqual(len(self.triggered_alarms), 1)
        self.assertEqual(len(self.manager.get_active_alarms()), 0)

    def test_multiple_alarms_fire_chronologically(self):
        self.manager.set_alarm(10.0, "T10")
        self.manager.set_alarm(30.0, "T30")
        self.manager.set_alarm(20.0, "T20")

        # Step 1: Advance to 1015 (only T10 due)
        self.simulated_time = 1015.0
        self.manager.tick()
        self.assertEqual([a.label for a in self.triggered_alarms], ["T10"])

        # Step 2: Advance to 1025 (T20 due)
        self.simulated_time = 1025.0
        self.manager.tick()
        self.assertEqual([a.label for a in self.triggered_alarms], ["T10", "T20"])

        # Step 3: Advance to 1035 (T30 due)
        self.simulated_time = 1035.0
        self.manager.tick()
        self.assertEqual([a.label for a in self.triggered_alarms], ["T10", "T20", "T30"])
        self.assertEqual(len(self.manager.get_active_alarms()), 0)

    def test_simultaneous_alarms_both_fire(self):
        self.manager.set_alarm(10.0, "Same1")
        self.manager.set_alarm(10.0, "Same2")

        self.simulated_time = 1010.0
        fired = self.manager.tick()

        self.assertEqual(len(fired), 2)
        self.assertEqual({a.label for a in fired}, {"Same1", "Same2"})
        self.assertEqual(len(self.manager.get_active_alarms()), 0)

    def test_cancel_active_alarm(self):
        alarm_id = self.manager.set_alarm(10.0, "To Cancel")
        self.assertTrue(self.manager.cancel_alarm(alarm_id))

        # Advance past target time
        self.simulated_time = 1020.0
        fired = self.manager.tick()

        self.assertEqual(len(fired), 0)
        self.assertEqual(len(self.triggered_alarms), 0)
        self.assertEqual(len(self.manager.get_active_alarms()), 0)

    def test_cancel_nonexistent_or_already_cancelled_alarm(self):
        # Non-existent
        self.assertFalse(self.manager.cancel_alarm(999))

        # Already cancelled
        alarm_id = self.manager.set_alarm(10.0, "Test")
        self.assertTrue(self.manager.cancel_alarm(alarm_id))
        self.assertFalse(self.manager.cancel_alarm(alarm_id))

    def test_cancel_already_fired_alarm(self):
        alarm_id = self.manager.set_alarm(5.0, "Fast")
        self.simulated_time = 1005.0
        self.manager.tick()
        self.assertEqual(len(self.triggered_alarms), 1)

        # Attempt to cancel after firing
        self.assertFalse(self.manager.cancel_alarm(alarm_id))


class TestConcurrencyAndRaceConditions(unittest.TestCase):
    """Tests race conditions between cancel and fire, and background thread execution."""

    def test_cancel_in_flight_firing_alarm_prevents_trigger(self):
        """Simulates the race where an alarm is popped into firing_alarms,
        and cancel_alarm is called before the trigger callback runs.
        """
        now = 100.0
        trigger_called = []

        def slow_trigger(alarm: Alarm):
            trigger_called.append(alarm.id)

        manager = AlarmManager(time_fn=lambda: now, on_trigger=slow_trigger)
        alarm_id = manager.set_alarm(1.0, "Race")

        # Advance time so alarm becomes due
        now = 102.0

        # Step 1: Simulate worker pulling alarm into firing_alarms
        with manager.lock:
            alarm = manager.active_alarms.pop(alarm_id)
            manager.firing_alarms[alarm_id] = alarm

        # Step 2: Concurrently, user calls cancel_alarm(alarm_id)
        cancelled = manager.cancel_alarm(alarm_id)
        self.assertTrue(cancelled)
        self.assertTrue(alarm.is_cancelled)

        # Step 3: Worker tries to execute the trigger
        with manager.lock:
            was_cancelled = alarm.is_cancelled
            manager.firing_alarms.pop(alarm.id, None)

        if not was_cancelled:
            manager.on_trigger(alarm)

        # Verification: on_trigger was aborted!
        self.assertEqual(trigger_called, [])

    def test_worker_thread_realtime_execution_and_cancellation(self):
        """Tests the actual background worker thread with real-time operations."""
        triggered = []

        def trigger(alarm: Alarm):
            triggered.append(alarm.id)

        manager = AlarmManager(tick_interval=0.05, on_trigger=trigger)
        manager.start()

        try:
            # 1. Alarm that should fire
            id_fire = manager.set_alarm(0.05, "WillFire")

            # 2. Alarm that will be cancelled before it fires
            id_cancel = manager.set_alarm(0.3, "WillCancel")

            # Wait briefly, then cancel the second alarm
            time.sleep(0.02)
            self.assertTrue(manager.cancel_alarm(id_cancel))

            # Wait for first alarm to fire
            time.sleep(0.1)

            self.assertIn(id_fire, triggered)
            self.assertNotIn(id_cancel, triggered)

        finally:
            # Verify clean and quick shutdown
            t_start = time.time()
            manager.stop()
            t_stop = time.time()
            self.assertLess(t_stop - t_start, 0.5)


if __name__ == "__main__":
    unittest.main()

