
import logging

from enums import *

from signal_requirements import *
import signal_config
import jmri
import time

SIGNAL_CONFIG_FILE = '/Users/svl/signal_server/signal_config.yaml'
SVL_JMRI_SERVER_HOST = 'http://svl-jmri.local:12080'

# root_logger = logging.getLogger()
logging.basicConfig(level=logging.DEBUG)

# TODO: JMRI handle should be passed in to SignalMast __init__.


class LayoutContext(object):
	def __init__(self, turnout_state, sensor_state, memory_vars):
		self.turnout_state = turnout_state
		self.sensor_state = sensor_state
		self.memory_vars = memory_vars

def main():
	signal_config_entries_by_name = signal_config.LoadConfig(SIGNAL_CONFIG_FILE)
	jmri_handle = jmri.JMRI(SVL_JMRI_SERVER_HOST)

	context = LayoutContext(jmri_handle.GetCurrentTurnoutData(),
		                    jmri_handle.GetCurrentSensorData(),
		                    jmri_handle.GetMemoryVariables())

	for mast in signal_config_entries_by_name.itervalues():
		logging.info('Configuring signal mast %s', mast)
		aspect = mast.GetIntendedAspect(context)
		logging.info('  Intended aspect is %s', aspect)
		jmri_handle.SetSignalHead(mast._mast_name, aspect)

if __name__ == '__main__':
	while True:
		try:
			main()
		except Exception as e:
			logging.exception(e)
		time.sleep(.5)
