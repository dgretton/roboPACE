import os, logging
from auxpump import PACKAGE_PATH, CONFIG
from .remote import remote_exec
_REMOTE_SCRIPT_DIR = CONFIG['remote_script_dir']
_REMOTE_RUN_SCRIPT = os.path.join(_REMOTE_SCRIPT_DIR, CONFIG['run_script'])
_REMOTE_ABORT_SCRIPT = os.path.join(_REMOTE_SCRIPT_DIR, CONFIG['abort_script'])

class NetworkDeckPumps:
    def __init__(self, disable=False):
        self.pump_bat_path = os.path.join(os.path.dirname(PACKAGE_PATH), 'auxpump_bat')
        self.disabled = disable

    def _run(self, run_cmd, *run_args):
        remote_cmd_tup = ('python', _REMOTE_RUN_SCRIPT, run_cmd, *run_args)
        if self.disabled: 
            logging.info(str(remote_cmd_tup) + ' would be executed remotely here')
            return
        logging.info('Running deck pump action "' + run_cmd + '" with ' + 
                ('args ' + ', '.join(run_args) if run_args else 'no args'))
        remote_exec(*remote_cmd_tup)

    def _run_direct(self, pump_ids_to_vols):
        # make sure is wrapped in quotes so that it will be interpreted as one string argument
        self._run('direct_cmd', repr(str(pump_ids_to_vols)))

    def disable(self):
        self.disabled = True

    def enable(self):
        self.disabled = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        remote_exec('.', _REMOTE_ABORT_SCRIPT) # '.' for source
