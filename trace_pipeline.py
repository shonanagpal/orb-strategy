#!/usr/bin/env python3
"""
Add this to each component to trace a specific symbol through the pipeline.
Set TRACE_SYMBOL env var to enable.
"""
import os
import datetime as dt

TRACE_SYMBOL = os.getenv("TRACE_SYMBOL", "")

def trace(component, event, symbol="", extra=""):
      timestamp = dt.datetime.now().strftime("%H:%M:%S.%f")[:-3]
      print(f"🔍 [{timestamp}] {component:12} | {event:30} | {extra}")
