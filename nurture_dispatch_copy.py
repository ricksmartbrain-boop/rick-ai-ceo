#!/usr/bin/env python3
from pathlib import Path
import runpy
import os
os.environ.update({k:v for k,v in [line.strip().split('=',1) for line in open('/Users/rickthebot/clawd/config/rick.env') if '=' in line and not line.startswith('#')] if k=='RESEND_API_KEY'})
runpy.run_path('/Users/rickthebot/rick-vault/scripts/nurture-dispatch.py', run_name='__main__')
