import logging
from enums import *
from signal_requirements import *
import yaml


class SignalMast(object):

    def __init__(self, mast_name, dispatch_config=None):
        """Initializer.

        mast_name: str, JMRI signal mast name being described
        dispatch_config: DispatchConfig or None
        """
        self._mast_name = mast_name
        self._dispatch_config = dispatch_config
        # list of SignalRoute instances
        self._routes = []

    def AddRoute(self, route):
        self._routes.append(route)

    def __str__(self):
        return self._mast_name

    def GetIntendedAspect(self, context):
        """context is a LayoutContext object."""
        logging.debug('  Determining aspect for signal %s', self)

        if context.memory_vars.get(SVL_DISPATCH_SIGNAL_CONTROL_MEMORY_VAR_NAME):
            if self._dispatch_config:
                logging.debug('  Can be configured by dispatch var %s in direction %s',
                              self._dispatch_config.memory_var_name, self._dispatch_config.direction)
                var_value = context.memory_vars.get(self._dispatch_config.memory_var_name)
                if var_value:
                    logging.debug('  JMRI has a value for this variable: %s', var_value)
                    value_parts = var_value.split(':')
                    if len(value_parts) != 3:
                        logging.error('Invalid memory contents: %s', var_value) 
                    clear = 'Authorized ' + self._dispatch_config.direction
                    occupied = 'Occupied ' + self._dispatch_config.direction
                    if value_parts[1] == clear:
                        # TODO: Figure out if clearance is ending by looking at forward signals
                        return SIGNAL_CLEAR
                    elif value_parts[1] == occupied:
                        return SIGNAL_RESTRICTING
                    else:
                        logging.debug('  No dispatch clearance; got %s', clear)

            return SIGNAL_STOP


        generated_aspects = []
        for i, route in enumerate(self._routes):
            logging.debug('    Checking route %s', i)
            aspect_for_route = route.GetBestAspect(context)
            if aspect_for_route != SIGNAL_STOP:
                logging.debug('    Route %s was %s', i, aspect_for_route)
                generated_aspects.append(aspect_for_route)
        if not generated_aspects:
            logging.debug('  Signal %s has no non-stop routes', self)
            return SIGNAL_STOP
        if len(generated_aspects) > 1:
            logging.error('  Signal %s has two non-stop routes! Using SIGNAL_STOP.', self)
            return SIGNAL_STOP
        logging.debug('  Signal %s is %s', self, generated_aspects[0])
        return generated_aspects[0]        


class SignalRoute(object):

    def __init__(self, next_mast_name=None, is_diverging=False):
        self._requirements = []  # list of Requirement instances
        self._is_diverging = is_diverging
        self._next_mast_name = next_mast_name # may be None

    def AddRequirement(self, requirement):
        self._requirements.append(requirement)

    def GetBestAspect(self, context):
        for i, req in enumerate(self._requirements):
            if not req.IsSatisfied(context.turnout_state, context.sensor_state):
                logging.debug('  Requirement %s unsatisfied', req)
                return SIGNAL_STOP
        logging.debug('  All Requirements satisfied')
        # TODO: consider next signal.
        aspect = SIGNAL_CLEAR
        if self._is_diverging:
            return ConvertAspectToDivergingAspect(aspect)
        return aspect


class DispatchConfig(object):
    def __init__(self, memory_var_name, direction):
        # A system or user name of a JMRI memory
        # variable updated by the SVL Dispatcher.
        self.memory_var_name = memory_var_name
        # 'NB' or 'SB'.
        self.direction = direction


def ParseRoute(route_config, is_diverging=False):
    route = SignalRoute(route_config.get('next_signal'), is_diverging)
    if 'requirements' not in route_config:
        raise AttributeError('Route config must have requirements')

    for requirement in route_config['requirements']:
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
            route.AddRequirement(TurnoutRequirement(turnout_name, state))
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

            route.AddRequirement(SensorRequirement(sensor_name, state))
        else:
            raise AttributeError(
                'Signal requirement must have turnout or sensor')
    return route

def LoadConfig(config_file_path):
    """Returns a map of {signal_name -> SignalConfigEntry}."""
    config_data = yaml.load(open(config_file_path, 'r'))
    signal_entries = {}  # name -> SignalConfigEntry
    for mast_name, configuration in config_data.items():
        logging.info('Parsing requirements for mast %s', mast_name)

        dispatch_config = None
        if 'dispatch_control' in configuration:
            d = configuration['dispatch_control']
            dispatch_config = DispatchConfig(d['memory_var'], d['direction'])

        signal = SignalMast(mast_name, dispatch_config)

        if 'normal_route' not in configuration:
            raise AttributeError('Signal must have a normal_route')
        signal.AddRoute(ParseRoute(configuration['normal_route']))

        if 'diverging_route' in configuration:
            signal.AddRoute(ParseRoute(configuration['diverging_route'],
                                       is_diverging=True))

        signal_entries[mast_name] = signal
    return signal_entries
