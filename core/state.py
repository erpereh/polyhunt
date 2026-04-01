"""Shared bot state — single source of truth for run_event."""
import threading

# Cleared on startup → bot starts PAUSED.
# set()   → bot runs cycles
# clear() → bot pauses after current cycle
run_event = threading.Event()
