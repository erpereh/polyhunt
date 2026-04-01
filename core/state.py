"""Shared bot state — single source of truth for run_event and stop_requested."""
import threading

# Cleared on startup → bot starts PAUSED.
# set()   → bot runs cycles
# clear() → bot pauses after current cycle
run_event = threading.Event()

# Signals that a stop has been requested but the current cycle is still running.
# set()   → stop requested, cycle in progress (state = "stopping")
# clear() → no stop pending
stop_requested = threading.Event()
