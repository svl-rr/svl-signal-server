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
from threading import Thread, RLock
from selenium import webdriver
import sys
import urllib.parse

DIR = os.path.dirname(os.path.abspath(__file__))

SIGNAL_CONFIG_FILE = os.path.join(DIR, 'signal_config.yaml')
SVL_JMRI_SERVER_HOST = 'http://127.0.0.1:12080'
SECONDS_BETWEEN_POLLS = 1.5

# This could be smarter if we listened to requests
# from freshly-booted nodes.
SECONDS_BETWEEN_FULL_LCC_CACHE_BROADCAST = None

SCRAPE_PANELS_ON_STARTUP = False

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

    for aspect_name, aspect_num in aspects.items():
        aspect = etree.Element('aspect', defines=aspect_name)
        number = etree.Element('number')
        number.text = str(aspect_num)
        aspect.append(number)
        head.append(aspect)

    return head


def OutputXML():
    signal_masts_by_name = signal_config.LoadConfig(SIGNAL_CONFIG_FILE)
    signalheads = etree.Element('signalheads')
    for name, mast in signal_masts_by_name.items():
        if type(mast) == signal_config.DoubleHeadTriLightMast:
            upper = _SignalHeadTree(mast._upper_head_address, mast._mast_name + '_upper')
            signalheads.append(upper)
            lower = _SignalHeadTree(mast._lower_head_address, mast._mast_name + '_lower')
            signalheads.append(lower)
        elif type(mast) == signal_config.SingleHeadTriLightMast:
            signalheads.append(_SignalHeadTree(mast._head_address, mast._mast_name))

    print(etree.tostring(signalheads, pretty_print=True))


class OpenlcbLayoutHandle(object):
    def __init__(self, openlcb_network):
        self._network = openlcb_network
        self._s_lock = RLock()
        self._s = None
        self._InitSocket()
        # {mast_name -> (first_eventid, appearance)}
        self._cache = {}
        self._last_broadcast_time = time.time()
        self._recv_thread = Thread(target=self._CheckForIncomingLCCData)
        self._recv_thread.daemon = True
        self._recv_thread.start()
        self._rcv_data = ''

    def _InitSocket(self):
        logging.info('Initializing OpenLCB Hub socket')
        try:
            if self._s: del self._s
            self._s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._s.settimeout(1.0)
            self._s.connect(('localhost', 12021))
        except:
            logging.exception("Error initializing OLCB socket")

    def _RemoveJunk(self, eventid):
        return (eventid
                .replace(' ', '')
                .replace(':', '')
                .replace('.', ''))

    def _CheckForIncomingLCCData(self):
        while True:
            with self._s_lock:
                try:
                    logging.debug("Checking for LCC data...")
                    data = self._s.recv(4096)
                    logging.debug('got data: "%s"' % data)
                    self._rcv_data += data
                except socket.timeout:
                    pass
                except:
                    logging.exception('LCC data check failed')

                while True:
                    self._rcv_data = self._rcv_data.lstrip()
                    logging.debug('In recv queue: "%s"', self._rcv_data)
                    semicolon_idx = self._rcv_data.find(';')
                    if semicolon_idx == -1:
                        logging.debug('Recv buffer does not contain an end of frame')
                        break
                    if not self._rcv_data.startswith(':X'):
                        # chop off invalid prefix data
                        self._rcv_data = self._rcv_data[semicolon_idx + 1:]
                        continue
                    packet = self._rcv_data[:semicolon_idx]
                    self._ProcessCANPacket(packet)
                    self._rcv_data = self._rcv_data[semicolon_idx + 1:]

            time.sleep(1)

    def _ProcessCANPacket(self, packet):
        logging.debug('processing packet: "%s"', packet)
        # Chop off ":X" and ";" garbage.
        packet = packet[2:-1]
        logging.debug('Full packet: "%s"', packet)
        packet_parts = packet.split('N')
        if len(packet_parts) != 2:
            logging.error("Ignoring invalid packet")
            return
        header, data = packet_parts
        logging.debug('Header: "%s" Data: "%s"', header, data)
        header_bin = bin(int(header, 16))[2:]
        logging.debug('Header binary: %s', header_bin)
        logging.debug('Header hex: %s', hex(int(header_bin, 2)))
        if len(header_bin) != 29:
            logging.error('Ignoring invalid-len header')
            return
        if header_bin[1] != '1':
            logging.debug('Ignoring non-openlcb frame')
            return
        hdr_frame_type = int(header_bin[2:5], 2)
        logging.debug('Frame type: %s', hdr_frame_type)
        if hdr_frame_type in [2, 3, 4, 5]:
            logging.debug('Ignoring datagram frame (type %s)', hdr_frame_type)
            return
        elif hdr_frame_type in [0, 6]:
            logging.debug('Ignoring reserved frame (type %s)', hdr_frame_type)
            return
        elif hdr_frame_type == 7:
            logging.debug('Ignoring stream frame (type %s)', hdr_frame_type)
            return
        elif hdr_frame_type != 1:
            logging.debug('Ignoring unknown frame (type %s)', hdr_frame_type)
            return
        can_mti_bin = header_bin[5:17]
        logging.debug("binary mti: %s", can_mti_bin)
        can_mti = hex(int(header_bin[5:17], 2))

        mti_descriptions = {
            '0x100': 'Initialization Complete (Full)',
            '0x101': 'Initialization Complete (Simple)',

            '0x488': 'Verify NodeID (Addressed)',
            '0x490': 'Verify NodeID (Global)',

            '0x170': 'Verified NodeID (Full)',
            '0x171': 'Verified NodeID (Simple)',

            '0x68': 'Optional Interaction Rejected',

            '0xa8': 'Terminate due to Error',

            '0x4a4': 'Consumer Range Identified',
            '0x4c4': 'Consumer Identified and State=Valid',
            '0x4c5': 'Consumer Identified and State=Invalid',
            '0x4c7': 'Consumer Identified and State=Unknown',

            '0x524': 'Producer Range Identified',
            '0x544': 'Producer Identified and State=Valid',
            '0x545': 'Producer Identified and State=Invalid',
            '0x547': 'Producer Identified and State=Unknown',

            '0x5b4': 'P/C Event Report',

            '0x668': 'Protocol Support Reply',
            '0x828': 'Protocol Support Inquiry',

            '0x8f4': 'Identify Consumer',
            '0x914': 'Identify Producer',

            '0xa08': 'SNIP Reply',
            '0xde8': 'SNIP Request',
        }

        description = mti_descriptions.get(can_mti, 'Unknown Packet with MTI %s' % can_mti)
        logging.info('Processing incoming packet (%s)', description)
        if can_mti not in ['0x100', '0x101']:
            return

        logging.info('Broadcasting cache!')
        self._BroadcastCache()

    def SetTriLightSignalHeadAppearance(self, mast_name, head_first_eventid, appearance, ignore_cache=False):
        if not ignore_cache:
            if self._cache.get(mast_name) == (head_first_eventid, appearance):
                logging.debug('  Aspect of %s is already %s', mast_name, appearance)
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
        logging.debug('  Head\'s first EventId: %s', first_eventid)
        appearance_eventid = hex(int(first_eventid, base=16) + event_offset)
        # strip 0x from the front
        appearance_eventid = str(appearance_eventid)[2:].upper()
        # left pad with 0 until len matches original
        appearance_eventid = appearance_eventid.rjust(len(first_eventid), '0')
        logging.debug('  Appearance eventid: %s (first+%s)', appearance_eventid, event_offset)

        # TODO: get rid of this magic prefix for "send an eventid"
        can_frame = ':X195B46ADN{};\n'.format(
            self._RemoveJunk(appearance_eventid))
        self._Send(can_frame)
        self._cache[mast_name] = (head_first_eventid, appearance)

    def SetLampAppearance(self, lamp_first_eventid, appearance, ignore_cache=False):
        assert appearance in ["ON", "FLASHING", "OFF"]

        if not ignore_cache:
            if self._cache.get(lamp_first_eventid) == appearance:
                logging.debug('  Appearance of lamp at address %s is already %s', lamp_first_eventid, appearance)
                return

        logging.debug('CPL Appearance')
        event_offset = {
            "ON": 0,
            "FLASHING": 1,
            "OFF": 2,
        }.get(appearance, 2)

        first_eventid = self._RemoveJunk(lamp_first_eventid)
        logging.debug("  Lamp's first EventId: %s", first_eventid)
        appearance_eventid = hex(int(first_eventid, base=16) + event_offset)
        # strip 0x from the front
        appearance_eventid = str(appearance_eventid)[2:].upper()
        # left pad with 0 until len matches original
        appearance_eventid = appearance_eventid.rjust(len(first_eventid), '0')
        logging.debug('  Appearance eventid: %s (first+%s)', appearance_eventid, event_offset)

        # TODO: get rid of this magic prefix for "send an eventid"
        can_frame = ':X195B46ADN{};\n'.format(
            self._RemoveJunk(appearance_eventid))
        self._Send(can_frame)
        self._cache[lamp_first_eventid] = appearance

    def _Send(self, frame):
        with self._s_lock:
            logging.info('  Sending LCC CAN packet %s', frame)
            try:
                err = self._s.sendall(frame.encode())
            except Exception as e:
                err = e
            if err is not None:
                logging.exception('Send to socket failed', err)
                self._InitSocket()

    def _BroadcastCache(self):
        logging.info('Time to rebroadcast LCC cache')
        for mast_name, (first_eventid, appearance) in self._cache.items():
            self.SetTriLightSignalHeadAppearance(mast_name, first_eventid, appearance, ignore_cache=True)


def Update(jmri_handle, openlcb_handle, reset_terminal=False):
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
            logging.debug('Configuring signal mast %s', mast)
            if mast.PostToOpenlcb():
                summary = mast.PutAspect(context, layout_handle=openlcb_handle, jmri_for_mem=jmri_handle)
            else:
                summary = mast.PutAspect(context, layout_handle=jmri_handle, jmri_for_mem=jmri_handle)
            table.add_row([str(mast), summary.aspect, summary.appearance, summary.reason])

        signaling_mode_suffix = ' [Block Signaling]'
        if signal_config._DispatchSignalingMode(context):
            signaling_mode_suffix = ' [Dispatch Signaling]'

        if reset_terminal:
            print(chr(27) + "[2J")
        print('Signal Server Status' + signaling_mode_suffix)
        print(table)

    except Exception as e:
        logging.exception(e)
        print('ERROR!   ' + str(e) + '\n')
        traceback.print_exc()
        print('')


def ScrapePanels(interval_sec):
    driver = webdriver.Safari(quiet=True, keep_alive=False)
    USER_PANELS = 'http://127.0.0.1:3000/web/svg/userPanels/index.svg'
    try:
        driver.implicitly_wait(3)
        driver.get(USER_PANELS)
        urls = set()
        for link in driver.find_elements_by_tag_name('a'):
            target = link.get_attribute('xlink:href')
            if '.svg' not in target:
                continue
            url = urllib.parse.urljoin(USER_PANELS, target)
            if url not in urls:
                urls.add(url)
                print('Adding URL', url)

        USE_THREADS = False

        if USE_THREADS:
            threads = []
            for url in urls:
                t = Thread(target=Scrape, args=(url,))
                t.daemon = True
                t.start()
                threads.append(t)

            for t in threads:
                t.join(10)
        else:
            for url in sorted(urls):
                driver.get(url)
                time.sleep(0.5)

    except:
        logging.exception('Webdriver failed')
        #raise
    finally:
        driver.quit()

def Scrape(url):
    driver = webdriver.Safari(quiet=True, keep_alive=False)
    driver.get(url)
    time.sleep(2)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--fake_jmri', type=bool, default=False)
    parser.add_argument('--pretty', type=bool, default=False)
    parser.add_argument('--output_xml', type=bool, default=False)
    parser.add_argument('--scrape_panel_interval_sec', type=int, default=20)
    args = parser.parse_args()

    logging_args = {
        'format': '%(asctime)s %(filename)s:%(lineno)d %(message)s',
        'level': logging.DEBUG,
    }
    # if args.pretty or args.output_xml:
    logging_args['filename'] = 'svl_signal_server.log'

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

    if SCRAPE_PANELS_ON_STARTUP:
        ScrapePanels(interval_sec=args.scrape_panel_interval_sec)

    openlcb_handle = OpenlcbLayoutHandle(None)

    while True:
        Update(jmri_handle, openlcb_handle, reset_terminal=args.pretty)
        if args.pretty:
            print('Last Update:', time.ctime(time.time()))
        time.sleep(SECONDS_BETWEEN_POLLS)


if __name__ == '__main__':
    main()
