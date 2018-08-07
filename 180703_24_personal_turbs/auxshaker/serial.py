import os, time
from auxshaker import TEMP_PATH, BAT_PATH, CONFIG
plink_bat = os.path.join(BAT_PATH, CONFIG['plink_bat'])
serial_bat = os.path.join(BAT_PATH, CONFIG['serial_bat'])

def send_serial(cmd_str):
    temp_ver = 0
    fname = None
    while fname is None or fname in os.listdir(TEMP_PATH):
        fname = 'tmp' + str(temp_ver) + '.txt'
        temp_ver += 1
    fname = os.path.join(TEMP_PATH, fname)
    try:
        with open(fname, 'w+') as ser_file:
            ser_file.write(cmd_str)
        os.system(' '.join((plink_bat, serial_bat, CONFIG['putty_session'], fname)))
    finally:
        for i in range(3):
            try:
                os.remove(fname)
                return
            except FileNotFoundError:
                return
            except PermissionError:
                pass
            time.sleep(2)