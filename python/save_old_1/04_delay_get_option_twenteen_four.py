#!/usr/bin/env python3
# 04_delay_get_option_twenteen_four.py
# -------------------------------
# Verbesserte Version – mehr Robustheit, besseres Error-Handling, zentrale Request-Methode
# -------------------------------

from __future__ import annotations

import csv
import json
import os
import sys
import time
import logging
import urllib3
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional, Any, Dict, Tuple, List
from functools import wraps

import requests
from requests.adapters import HTTPAdapter