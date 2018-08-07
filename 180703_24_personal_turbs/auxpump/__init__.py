import os, json
from os.path import dirname, join, abspath
PACKAGE_PATH = abspath(dirname(__file__))
TEMP_PATH = join(PACKAGE_PATH, 'tmp')
CONFIG = None
with open(join(PACKAGE_PATH, 'config.json')) as conf:
    CONFIG = json.loads(conf.read())

from .pace import *
