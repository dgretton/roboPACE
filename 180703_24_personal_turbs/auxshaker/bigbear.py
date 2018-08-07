import os, json
from auxshaker import CONFIG
from .serial import send_serial

class Shaker:
    #TODO: Expose advanced features if needed.
    
    def __init__(self):
        self.disabled = False

    def start(self, rpm=350):
        if not 60 <= rpm <= 3570:
            raise ValueError('Speed setting for Big Bear HT-91108 Orbital Shaker not between 60 and 3570 RPM: ' + str(rpm))
        if not self.disabled:
            send_serial(CONFIG['start_cmd'].format(str(int(rpm))))

    def stop(self):
        if not self.disabled:
            send_serial(CONFIG['stop_cmd'])

    def disable(self):
        self.disabled = True

    def enable(self):
        self.disabled = False

