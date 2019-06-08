
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
from lxml import etree
import socket

DIR = os.path.dirname(os.path.abspath(__file__))

SIGNAL_CONFIG_FILE = os.path.join(DIR, 'signal_config.yaml')
SVL_JMRI_SERVER_HOST = 'http://svl-jmri.local:12080'
SECONDS_BETWEEN_POLLS = 1.5

# This could be smarter if we listened to requests
# from freshly-booted nodes.
SECONDS_BETWEEN_FULL_LCC_CACHE_BROADCAST = None

# TODO: JMRI handle should be passed in to SignalMast __init__.


class LayoutContext(object):
	def __init__(self, turnout_state, sensor_state, memory_vars, masts):
		self.turnout_state = turnout_state
		self.sensor_state = sensor_state
		self.memory_vars = memory_vars
		self.masts = masts

def _SignalHeadTree(address, name):
	CLASS = 'jmri.implementation.configurexml.DccSignalHeadXml'
	head = etree.Element('signalhead')
	head.attrib['class'] = CLASS

	system_name_text = 'NH$%s' % address
	head.attrib['systemName'] = system_name_text
	head.attrib['userName'] = name

	systemName = etree.Element('systemName')
	systemName.text = system_name_text
	head.append(systemName)

	userName = etree.Element('userName')
	userName.text = name
	head.append(userName)

	useAddressOffSet = etree.Element('useAddressOffSet')
	useAddressOffSet.text = 'no'
	head.append(useAddressOffSet)

	# Default NCE Light-It Aspect numbers.
	aspects = {
		'Red': 0,
		'Yellow': 1,
		'Green': 2,
		'Flashing Red': 3,
		'Flashing Yellow': 4,
		'Flashing Green': 5,

		# Not implemented.
		'Dark': 31,
		'Lunar': 31,
		'Flashing Lunar': 31,
	}

	for aspect_name, aspect_num in aspects.iteritems():
		aspect = etree.Element('aspect', defines=aspect_name)
		number = etree.Element('number')
		number.text = str(aspect_num)
		aspect.append(number)
		head.append(aspect)

	return head


def OutputXML():
	signal_masts_by_name = signal_config.LoadConfig(SIGNAL_CONFIG_FILE)
	signalheads = etree.Element('signalheads')
	for name, mast in signal_masts_by_name.iteritems():
		if type(mast) == signal_config.DoubleHeadMast:
			upper = _SignalHeadTree(mast._upper_head_address, mast._mast_name + '_upper')
			signalheads.append(upper)
			lower = _SignalHeadTree(mast._lower_head_address, mast._mast_name + '_lower')
			signalheads.append(lower)
		elif type(mast) == signal_config.SingleHeadMast:
			signalheads.append(_SignalHeadTree(mast._head_address, mast._mast_name))

	print etree.tostring(signalheads, pretty_print=True)


class OpenlcbLayoutHandle(object):
	def __init__(self, openlcb_network):
		self._network = openlcb_network
		self._s = None
		# {mast_name -> (first_eventid, appearance)}
		self._cache = {}
		self._last_broadcast_time = time.time()

	def _RemoveJunk(self, eventid):
		return (eventid
			.replace(' ', '')
			.replace(':', '')
			.replace('.', ''))

	def SetSignalHeadAppearance(self, mast_name, head_first_eventid, appearance, ignore_cache=False):
		if not ignore_cache:
			self._MaybeBroadcastCache()

		if not ignore_cache:
			if self._cache.get(mast_name) == (head_first_eventid, appearance):
				logging.info('  Aspect of %s is already %s', mast_name, appearance)
				return
		event_offset = {
			HEAD_GREEN: 0,
			HEAD_YELLOW: 1,
			HEAD_RED: 2,
			HEAD_FLASHING_GREEN: 3,
			HEAD_FLASHING_YELLOW: 4,
			HEAD_FLASHING_RED: 5,
			HEAD_DARK: 6,
		}.get(appearance, 6)

		first_eventid = self._RemoveJunk(head_first_eventid)
		logging.info('  Head\'s first EventId: %s', first_eventid)
		appearance_eventid = hex(int(first_eventid, base=16)+event_offset)
		# strip 0x from the front
		appearance_eventid = str(appearance_eventid)[2:].upper()
		# left pad with 0 until len matches original
		appearance_eventid = appearance_eventid.rjust(len(first_eventid), '0')
		logging.info('  Appearance eventid: %s (first+%s)', appearance_eventid, event_offset)

		# TODO: get rid of this magic prefix for "send an eventid"
		can_frame = ':X195B46ADN{};\n'.format(
			self._RemoveJunk(appearance_eventid))
		self._Send(can_frame)
		self._cache[mast_name] = (head_first_eventid, appearance)

	def _Send(self, frame):
		logging.info('  Sending LCC CAN packet %s', frame)
		try:
			if not self._s:
				self._InitSocket()
		except NameError:
			self._InitSocket()
		try:
			err = self._s.sendall(frame)
		except Exception as e:
			err = e
		if err is not None:
			del self._s
			raise RuntimeError('Send to socket failed: %s' % err)

	def _InitSocket(self):
		del self._s
		self._s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
		self._s.connect(('localhost', 12021))

	def _MaybeBroadcastCache(self):
		if not SECONDS_BETWEEN_FULL_LCC_CACHE_BROADCAST:
			return
		now = time.time()
		seconds_since_last_broadcast = now - self._last_broadcast_time
		if seconds_since_last_broadcast > SECONDS_BETWEEN_FULL_LCC_CACHE_BROADCAST:
			logging.info('Time to rebroadcast LCC cache')
			for mast_name, (first_eventid, appearance) in self._cache.iteritems():
				self.SetSignalHeadAppearance(mast_name, first_eventid, appearance, ignore_cache=True)
			self._last_broadcast_time = now
		else:
			logging.info('Only been %s seconds since last broadcast', seconds_since_last_broadcast)


def Update(jmri_handle, openlcb_handle):
	try:
		signal_masts_by_name = signal_config.LoadConfig(SIGNAL_CONFIG_FILE)
		
		context = LayoutContext(jmri_handle.GetCurrentTurnoutData(),
			                    jmri_handle.GetCurrentSensorData(),
			                    jmri_handle.GetMemoryVariables(),
			                    signal_masts_by_name)

		table = prettytable.PrettyTable()
		table.field_names = ['Mast', 'Aspect', 'Appearance', 'Reason']

		for mast_name in sorted(signal_masts_by_name.keys(), key=lambda s: s.lower()):
			mast = signal_masts_by_name[mast_name]
			logging.info('Configuring signal mast %s', mast)
			if mast.PostToOpenlcb():
				summary = mast.PutAspect(context, openlcb_handle)
			else:
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
	parser.add_argument('--output_xml', type=bool, default=False)
	args = parser.parse_args()

	logging_args = {
		'format': '%(asctime)s %(filename)s:%(lineno)d %(message)s',
		'level': logging.DEBUG,
	}
	if args.pretty or args.output_xml:
		logging_args['filename'] = '/tmp/signal_server.log'

	logging.basicConfig(**logging_args)

	if args.output_xml:
		OutputXML()
		return

	if args.fake_jmri:
		jmri_handle = jmri.FakeJMRI()
	else:
		jmri_handle = jmri.JMRI(SVL_JMRI_SERVER_HOST)

	# openlcb_network = openlcb_python.tcpolcblink.TcpToOlcbLink()
	# openlcb_network.host = 'localhost'
	# openlcb_network.port = 12021

	openlcb_handle = OpenlcbLayoutHandle(None)

	while True:
		if args.pretty:
			print(chr(27) + "[2J")
		Update(jmri_handle, openlcb_handle)
		if args.pretty:
			print 'Last Update: ' + time.ctime(time.time())
		time.sleep(SECONDS_BETWEEN_POLLS)

	

if __name__ == '__main__':
	main()
