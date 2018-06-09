import logging
from enums import *
from signal_requirements import *
import yaml


class SignalMast(object):

    def __init__(self, mast_name, target_mast):
        """Initializer.

        mast_name: str, JMRI signal mast name being described
        target_mast: str, JMRI signal mast name after this one
        """
        self._mast_name = mast_name
        self._target_mast = target_mast
        # list of SignalRequirement instances
        self._requirements = []

    def ParseAndAddRequirement(self, requirement):
        if 'turnout' in requirement:
            turnout_name = requirement['turnout']
            raw_state = requirement['state'].lower()
            state = {
                'thrown': TURNOUT_THROWN,
                'closed': TURNOUT_CLOSED,
            }.get(raw_state)
            if not state:
                raise AttributeError('Invalid turnout state %s', raw_state)
            logging.info('  Parsed turnout requrement %s %s',
                         turnout_name, state)
            self._requirements.append(TurnoutRequirement(turnout_name, state))
        elif 'sensor' in requirement:
            sensor_name = requirement['sensor']
            raw_state = requirement.get('state', False)
            state = {
                True: SENSOR_ACTIVE,
                False: SENSOR_INACTIVE,
            }.get(raw_state)
            if not state:
                raise AttributeError('Invalid sensor state %s', raw_state)
            logging.info('  Parsed sensor requirement %s %s',
                         sensor_name, state)
            self._requirements.append(SensorRequirement(sensor_name, state))
        else:
            raise AttributeError(
                'Signal requirement must have turnout or sensor')

    def __str__(self):
    	return self._mast_name

    def GetIntendedAspect(self, all_turnouts, all_sensors):
        logging.debug('Computing intended aspect for %s', self._mast_name)
        # if any of sensors, turnouts not satisfied, aspect is stop.
        for req in self._requirements:
            if not req.IsSatisfied(all_turnouts, all_sensors):
                logging.debug('  Aspect is STOP because requirement %s unsatisfied',
                              req)
                return SIGNAL_STOP

        logging.debug('  All direct requirements satisfied')
        # TODO: check next signal; for now, green.
        return SIGNAL_CLEAR


def LoadConfig(config_file_path):
    """Returns a map of {signal_name -> SignalConfigEntry}."""
    config_data = yaml.load(open(config_file_path, 'r'))
    signal_entries = {}  # name -> SignalConfigEntry
    for mast_name, configuration in config_data.items():
        dest_mast = configuration['destination_mast']
        new_entry = SignalMast(mast_name, dest_mast)
        requirement_data = configuration['requirements']
        logging.info('Parsing requirements for mast %s -> %s',
                     mast_name, dest_mast)
        if not requirement_data:
            raise AttributeError('Requirements cannot be empty')
        for req in requirement_data:
            new_entry.ParseAndAddRequirement(req)
        signal_entries[mast_name] = new_entry
    return signal_entries
