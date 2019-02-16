
import logging
import argparse

from enums import *

from signal_requirements import *
import signal_config
import jmri
import os
import traceback
import time
import prettytable

DIR = os.path.dirname(os.path.abspath(__file__))

SIGNAL_CONFIG_FILE = os.path.join(DIR, 'signal_config.yaml')
SVL_JMRI_SERVER_HOST = 'http://svl-jmri.local:12080'
SECONDS_BETWEEN_POLLS = 1.5

# TODO: JMRI handle should be passed in to SignalMast __init__.


class LayoutContext(object):
	def __init__(self, turnout_state, sensor_state, memory_vars):
		self.turnout_state = turnout_state
		self.sensor_state = sensor_state
		self.memory_vars = memory_vars

def Update(jmri_handle):
	try:
		signal_config_entries_by_name = signal_config.LoadConfig(SIGNAL_CONFIG_FILE)
		
		context = LayoutContext(jmri_handle.GetCurrentTurnoutData(),
			                    jmri_handle.GetCurrentSensorData(),
			                    jmri_handle.GetMemoryVariables())

		table = prettytable.PrettyTable()
		table.field_names = ['Mast', 'Aspect', 'Appearance', 'Reason']

		for mast in signal_config_entries_by_name.itervalues():
			logging.info('Configuring signal mast %s', mast)
			summary = mast.PutAspect(context, jmri_handle)
			table.add_row([str(mast), summary.aspect, summary.appearance, summary.reason])

		print table

	except Exception as e:
		logging.exception(e)
		print 'ERROR!   ' + str(e) + '\n'
		traceback.print_exc()
		print ''


def main():
	parser = argparse.ArgumentParser()
	parser.add_argument('--fake_jmri', type=bool, default=False)
	parser.add_argument('--pretty', type=bool, default=False)
	args = parser.parse_args()

	logging_args = {
		'format': '%(asctime)s %(filename)s:%(lineno)d %(message)s',
		'level': logging.DEBUG,
	}
	if args.pretty:
		logging_args['filename'] = '/tmp/signal_server.log'

	logging.basicConfig(**logging_args)

	if args.fake_jmri:
		jmri_handle = jmri.FakeJMRI()
	else:
		jmri_handle = jmri.JMRI(SVL_JMRI_SERVER_HOST)

	while True:
		if args.pretty:
			print(chr(27) + "[2J")
		Update(jmri_handle)
		if args.pretty:
			print 'Last Update: ' + time.ctime(time.time())
		time.sleep(SECONDS_BETWEEN_POLLS)

	

if __name__ == '__main__':
	main()
