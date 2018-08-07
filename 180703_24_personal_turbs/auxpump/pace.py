from .auxpump import NetworkDeckPumps

class OffDeckCulturePumps(NetworkDeckPumps):

    def clean_reservoir(self):
        self._run('clean')

    def prime_reservoir(self):
        self._run('prime')

    def fresh_reservoir(self):
        self._run('fresh')

    def refill_water_rinse(self):
        self._run('refill_rinse')

class PACEDeckPumps(OffDeckCulturePumps):
    pass # Backward compatibility

class LBPumps(NetworkDeckPumps):

    def bleach_clean(self):
        self._run('clean')

    def prime(self):
        self._run('prime')

    def refill(self, vol=4.0): # mL
        self._run('fresh', str(vol))

    def empty(self, vol=6.0):
        self._run('empty', str(vol))

    def refill_rinse(self): # TODO: Probably remove
        self._run('refill_rinse')
