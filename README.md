# Python CLI Alarm Clock

A minimalist, thread-safe CLI alarm clock built with Python's standard library. Designed to prioritize correctness, testability, and clear engineering trade-offs over feature count.

---

## Architecture Overview

```
                      ┌────────────────────────┐
                      │    User Input (CLI)    │
                      │      (Main Thread)     │
                      └───────────┬────────────┘
                                  │ set / cancel / list
                                  ▼
                      ┌────────────────────────┐
                      │      AlarmManager      │
                      │   (Thread-Safe Lock)   │
                      └───────────┬────────────┘
                                  │
          ┌───────────────────────┴───────────────────────┐
          ▼                                               ▼
┌──────────────────┐                            ┌───────────────────┐
│  active_alarms   │                            │   firing_alarms   │
│  {id: Alarm}     │                            │   {id: Alarm}     │
└─────────┬────────┘                            └─────────┬─────────┘
          │                                               │
          │                   tick()                      │
          └─────────────────────►─────────────────────────┘
                                  │
                                  ▼
                      ┌────────────────────────┐
                      │    Trigger Callback    │
                      │  (Outside Lock / I/O)  │
                      └────────────────────────┘
```

---

## Features: What, Why, and How

### 1. Single Worker Thread with Interruptible Wait

* **What**: A single background thread handles alarm evaluation and timing across all active alarms, rather than spawning a separate thread per alarm.
* **Why**:
  * **Eliminates thread proliferation**: Spawning a thread per alarm wastes OS thread handles and makes coordinated output and cancellation prone to race conditions.
  * **Interruptible responsiveness**: Normal `time.sleep()` cannot be interrupted until it finishes. By using `threading.Event.wait(timeout)`, new alarms, cancellations, or shutdown signals wake the worker instantly.
* **How**:
  * The worker computes the delay to the earliest alarm: `delay = min(target_epoch - now)`.
  * It waits using `self._wake_event.wait(timeout=min(delay, tick_interval))`.
  * `set_alarm()`, `cancel_alarm()`, and `stop()` call `_wake_event.set()`, waking the worker immediately to recalculate or exit.

---

### 2. Deterministic Time Injection (`time_fn`)

* **What**: `AlarmManager` accepts a pluggable time provider function (`time_fn: Callable[[], float] = time.time`).
* **Why**:
  * Real-time testing of countdowns is slow, flaky, and non-deterministic.
  * Injecting virtual time allows tests to simulate hours or days of elapsed time instantaneously.
* **How**:
  * Production code defaults to `time.time`.
  * In unit tests, a simulated clock (`fake_now`) is stepped manually:
    ```python
    simulated_time = 1000.0
    manager = AlarmManager(time_fn=lambda: simulated_time)
    manager.set_alarm(10.0)
    simulated_time = 1010.0
    fired = manager.tick()  # Fires immediately without sleeping
    ```

---

### 3. Race-Free Cancellation (`active_alarms` vs. `firing_alarms`)

* **What**: A two-stage lifecycle (`active` $\rightarrow$ `firing`) combined with an individual `cancel_event` per alarm.
* **Why**:
  * **The Phantom Ring Bug**: If an alarm's timer expires at the exact microsecond the user cancels it, a naive system pops the alarm to trigger it, leaving the cancel request to report `"Alarm not found"`, only for the alarm to ring moments later.
* **How**:
  * Maturing alarms are moved from `active_alarms` to `firing_alarms` under lock.
  * `cancel_alarm()` inspects both `active_alarms` and in-flight `firing_alarms`, setting `alarm.cancel_event.set()`.
  * Right before executing the alert callback outside the lock, `AlarmManager` verifies `alarm.is_cancelled`. If true, the trigger is aborted.

---

### 4. Lock Granularity & I/O Separation

* **What**: Terminal printing, audio bells, and notifications occur strictly outside of `threading.Lock`.
* **Why**:
  * Holding a lock during terminal I/O or sound playback blocks the main CLI thread, causing input lag or freezing `set_alarm` and `cancel_alarm`.
* **How**:
  * The lock is held only for dictionary lookups, dictionary pops, and timestamp comparisons ($\mathcal{O}(1)$ or quick $\mathcal{O}(N)$ in-memory operations taking $< 5\,\mu\text{s}$).
  * Triggering (`self.on_trigger(alarm)`) is executed outside the `with self.lock:` block.

---

### 5. Flexible Duration Parsing & ETA Formatting

* **What**: Pure utility functions `parse_duration()` and `format_seconds()` for handling user input and countdown displays.
* **Why**:
  * Provides intuitive CLI inputs (`10s`, `5m`, `1.5h`, or raw seconds `45`) without external CLI dependencies.
* **How**:
  * Normalizes input strings, handles unit suffixes (`s`, `m`, `h`), and validates against empty, non-numeric, or negative inputs.
  * Formats remaining seconds cleanly into human-readable strings (`1h 05m 12s` or `45s`).

---

### 6. Interactive CLI & Clean Signal Handling

* **What**: An interactive Read-Eval-Print Loop (REPL) offering `set`, `cancel`, `list`, `help`, and `exit`.
* **Why**:
  * Enables dynamic scheduling and canceling of multiple alarms concurrently without complex command-line syntax.
* **How**:
  * Main thread runs `input("alarm> ")`.
  * Traps `KeyboardInterrupt` (`Ctrl+C`) and `EOFError` to gracefully call `manager.stop()`, cleanly shutting down the background worker thread without raw tracebacks.

---

## Deliberate Non-Goals (What Was Left Out)

| Feature | Reason to Omit |
| :--- | :--- |
| **Thread-per-Alarm** | High resource overhead and prone to race conditions and display corruption. |
| **External Audio Libraries (`pygame`, `playsound`)** | Heavy C-extensions and platform-specific audio driver dependencies; standard ASCII bell (`\a`) is portable and zero-dependency. |
| **Persistent Storage / SQLite** | Adds state migration, file locks, and schema management not needed for transient CLI alarms. |
| **Background Daemonization / Forking** | Process daemonization is better handled by native OS tools (`systemd`, `tmux`, `nohup`). |

---

## Edge Cases Handled

* **Simultaneous Alarms**: Alarms scheduled for the exact same epoch both fire in the same tick.
* **Chronological Firing**: Alarms set in random order trigger strictly in order of due time.
* **Premature Wakeup**: Checks strictly enforce `now >= target_epoch` before firing.
* **Double Cancellation**: Canceling an already-cancelled or non-existent alarm cleanly returns `False`.
* **Negative or Malformed Input**: Handled gracefully with explicit `ValueError` messages.
* **Fast Shutdown**: Worker thread terminates promptly ($< 0.5\text{s}$) upon `stop()` by interrupting `_wake_event`.

---

## Getting Started

### Prerequisites
* Python 3.10+ (Standard library only; no external packages needed).

### Running the CLI
```bash
python3 cli.py
```

Example session:
```text
=======================================================
 ⏰ Python CLI Alarm Clock
 Type 'help' for commands, 'exit' or Ctrl+C to quit.
=======================================================
alarm> set 10s Coffee
✓ Alarm #1 scheduled for 10s from now ('Coffee')

alarm> set 5m Standup
✓ Alarm #2 scheduled for 5m 00s from now ('Standup')

alarm> list
Active alarms (2):
  #1 [Coffee]: 7s remaining
  #2 [Standup]: 4m 57s remaining

alarm> cancel 1
✓ Alarm #1 cancelled.

alarm> list
Active alarms (1):
  #2 [Standup]: 4m 48s remaining

alarm> exit
Shutting down worker...
Goodbye!
```

---

## Running Unit Tests

The test suite covers parsing, formatting, deterministic scheduling, concurrency races, and real-time execution:

```bash
python3 -m unittest test_alarm.py -v
```

