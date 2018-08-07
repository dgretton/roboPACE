import os, json
from os.path import dirname, join

PACKAGE_PATH = dirname(__file__)
TEMP_PATH = join(PACKAGE_PATH, 'tmp')
BAT_PATH = join(PACKAGE_PATH, 'bat')
with open(join(PACKAGE_PATH, 'config.json')) as f:
    CONFIG = json.loads(f.read())

from .bigbear import *
