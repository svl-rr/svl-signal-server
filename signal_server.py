
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


def main():
	signal_config_entries_by_name = signal_config.LoadConfig(SIGNAL_CONFIG_FILE)
	jmri_handle = jmri.JMRI(SVL_JMRI_SERVER_HOST)

	turnouts = jmri_handle.GetCurrentTurnoutData()
	sensors = jmri_handle.GetCurrentSensorData()

	for mast in signal_config_entries_by_name.itervalues():
		logging.info('Configuring signal mast %s', mast)
		aspect = mast.GetIntendedAspect(turnouts, sensors)
		logging.info('  Intended aspect is %s', aspect)
		jmri_handle.SetSignalHead(mast._mast_name, aspect)

if __name__ == '__main__':
	while True:
		try:
			main()
		except Exception as e:
			logging.exception(e)
		time.sleep(.5)
