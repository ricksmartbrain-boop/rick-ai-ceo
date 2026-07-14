#!/bin/bash
cd ~/.openclaw/workspace/skills/free-ride/jobs && python3 buyer-intent-radar.py 2>&1 | tail -20
