import logging
from enums import *
from signal_requirements import *
import yaml
import sys


def _GetNextMostPermissiveAspect(aspect):
    logging.debug('Computing next most permissive aspect for %s', aspect)
    if 'SIGNAL_APPROACH_CLEAR_' in aspect:
        return SIGNAL_CLEAR
    if 'SIGNAL_APPROACH_' in aspect:
        return SIGNAL_ADVANCE_APPROACH

    if 'SIGNAL_DIVERGING_' in aspect:
        return {
            SIGNAL_DIVERGING_CLEAR: SIGNAL_APPROACH_CLEAR_SIXTY,
            SIGNAL_DIVERGING_CLEAR_LIMITED: SIGNAL_APPROACH_CLEAR_FIFTY,
            SIGNAL_DIVERGING_ADVANCE_APPROACH: SIGNAL_APPROACH_CLEAR_FIFTY,
            SIGNAL_DIVERGING_APPROACH: SIGNAL_APPROACH_DIVERGING,
            SIGNAL_DIVERGING_RESTRICTING: SIGNAL_APPROACH_DIVERGING,
        }.get(aspect, SIGNAL_RESTRICTING)

    # Non-diverging aspects
    return {
        SIGNAL_CLEAR: SIGNAL_CLEAR,
        SIGNAL_ADVANCE_APPROACH: SIGNAL_CLEAR,
        SIGNAL_APPROACH: SIGNAL_ADVANCE_APPROACH,
        SIGNAL_RESTRICTING: SIGNAL_APPROACH,
        SIGNAL_STOP: SIGNAL_APPROACH,
        SIGNAL_DARK: SIGNAL_APPROACH,
    }.get(aspect, SIGNAL_RESTRICTING)


def _DispatchSignalingMode(context):
    return context.memory_vars.get(SVL_DISPATCH_SIGNAL_CONTROL_MEMORY_VAR_NAME).lower() == 'yes'

def _GetDispatchVarContent(context, var_name):
    """Returns (direction_in_var, reason)"""
    var_value = context.memory_vars.get(var_name)
    if not var_value:
        return (None, 'Dispatch var %s not found' % var_name)
    logging.info('  JMRI has a value for this variable: %s', var_value)
    value_parts = var_value.split(':')
    if len(value_parts) != 3:
        return (None, 'Invalid memory content: %s' % var_value)
    unk_junk, section_status, dispatcher_label = value_parts
    return (section_status, dispatcher_label), 'OK'



class SignalSummary(object):
    def __init__(self, aspect, appearance, reason):
        self.aspect = aspect.replace('SIGNAL_', '')
        self.appearance = appearance
        self.reason = reason

    @classmethod
    def PrettyAppearance(cls, head1, head2=None):
        head1 = head1.replace('HEAD_', '')
        if head2:
            head2 = head2.replace('HEAD_', '')
            return '%s over %s' % (head1, head2)
        else:
            return head1


class SignalMast(object):

    def __init__(self, mast_name):
        """Initializer.

        mast_name: str, signal mast name being described
        """
        self._mast_name = mast_name
        # list of SignalRoute instances
        self._routes = []

    def AddRoute(self, route):
        self._routes.append(route)

    def __str__(self):
        return self._mast_name

    def PostToOpenlcb(self):
        return False

    def HasRouteDispatchConfigs(self):
        return True
        for route in self._routes:
            if route._dispatch_config:
                return True
        return False

    def GetIntendedAspect(self, context):
        """Returns a SIGNAL_* instance; context is a LayoutContext object."""
        logging.debug('  Determining aspect for signal %s', self)
        reason = 'Unknown'

        generated_aspects = []
        no_aspect_reasons = []
        for i, route in enumerate(self._routes):
            logging.debug('    Checking route %s', route._route_name)
            aspect_for_route, reason = route.GetAspectOrNone(context)
            logging.debug('    Route %s was %s', route._route_name, aspect_for_route)
            if not aspect_for_route:
                logging.info('Mast %s Route %s has no aspect because %s', self._mast_name, route._route_name, reason)
                no_aspect_reasons.append('%s has %s' % (route._route_name, reason))
                continue
            generated_aspects.append((aspect_for_route, reason))
        if not generated_aspects:
            logging.debug('  Signal %s has no possible routes', self)
            return SIGNAL_STOP, ',\n'.join(no_aspect_reasons)
        if len(generated_aspects) > 1:
            logging.error('  Signal %s has two non-stop routes (%s)! Using SIGNAL_STOP.', self, generated_aspects)
            return SIGNAL_STOP, 'ERROR: Multiple routes possible'
        aspect, reason = generated_aspects[0]
        return aspect, reason

    def PutAspect(self, context, layout_handle):
        """Enacts the will of this SignalMast."""
        raise NotImplementedError

        aspect, reason = self.GetIntendedAspect(context)
        logging.debug('  Signal %s at %s: %s',
            self._mast_name, aspect, reason)
        layout_handle.SetSignalMastAspect(
            self._mast_name, 'unused_address', aspect)
        return SignalSummary(aspect, 'None (JMRI Mast)', reason)


class SingleHeadMast(SignalMast):
    def __init__(self, mast_name, head_address):
        super(SingleHeadMast, self).__init__(mast_name)
        self._head_address = head_address

    def PostToOpenlcb(self):
        # Check if head address looks like an event id.
        return len(str(self._head_address)) > 7

    @classmethod
    def GetAppearance(cls, aspect):
        # A 1-head mast on a diverging route is not great.
        if '_DIVERGING_' in aspect:
            old_aspect = aspect
            # SIGNAL_DIVERGING_CLEAR_LIMITED -> SIGNAL_APPROACH_CLEAR_FIFTY
            aspect = aspect.replace('_DIVERGING', '').replace('_CLEAR_LIMITED', '_APPROACH_CLEAR_FIFTY')
            logging.debug('Hackily replaced 1-head diverging aspect %s with simple aspect %s',
                         old_aspect, aspect)
            if aspect == SIGNAL_CLEAR:
                # At least show a flashing green when diverging.
                aspect = SIGNAL_APPROACH_CLEAR_FIFTY
        if aspect == SIGNAL_CLEAR:
            return HEAD_GREEN
        elif 'APPROACH_CLEAR' in aspect:
            return HEAD_FLASHING_GREEN
        elif aspect == SIGNAL_ADVANCE_APPROACH:
            return HEAD_FLASHING_YELLOW
        elif 'APPROACH' in aspect:
            return HEAD_YELLOW
        elif aspect == SIGNAL_RESTRICTING:
            return HEAD_FLASHING_RED
        elif aspect == SIGNAL_DARK:
            return HEAD_DARK


        return HEAD_RED

    def PutAspect(self, context, layout_handle):
        aspect, reason = self.GetIntendedAspect(context)
        appearance = self.GetAppearance(aspect)
        logging.debug('  %s mapped aspect %s to %s [%s]',
            self._mast_name, aspect, appearance, reason)
        layout_handle.SetSignalHeadAppearance(self._mast_name, self._head_address, appearance)
        return SignalSummary(
            aspect,
            SignalSummary.PrettyAppearance(appearance),
            reason)


class DoubleHeadMast(SignalMast):
    def __init__(
            self, mast_name,
            upper_head_address, lower_head_address):
        super(DoubleHeadMast, self).__init__(mast_name)
        self._upper_head_address = upper_head_address
        self._lower_head_address = lower_head_address

    def PostToOpenlcb(self):
        # Check if upper head address looks like an event id.
        upper_head_looks_eventy = len(str(self._upper_head_address)) > 7
        lower_head_looks_eventy = len(str(self._lower_head_address)) > 7
        if upper_head_looks_eventy != lower_head_looks_eventy:
            raise AttributeError('In %s: upper head and lower head must neither or both be event IDs' % self._mast_name)
        return upper_head_looks_eventy 

    def PutAspect(self, context, layout_handle):
        aspect, reason = self.GetIntendedAspect(context)
        upper_appearance = lower_appearance = HEAD_RED
        if 'DIVERGING' in aspect:
            if aspect == SIGNAL_APPROACH_DIVERGING:
                upper_appearance = lower_appearance = HEAD_YELLOW
            else:
                # all other *DIVERGING* aspects are red over <something>
                lower_appearance = {
                    SIGNAL_DIVERGING_CLEAR: HEAD_GREEN,
                    SIGNAL_DIVERGING_CLEAR_LIMITED: HEAD_FLASHING_GREEN,
                    SIGNAL_DIVERGING_ADVANCE_APPROACH: HEAD_FLASHING_YELLOW,
                    SIGNAL_DIVERGING_APPROACH: HEAD_YELLOW,
                    SIGNAL_DIVERGING_RESTRICTING: HEAD_FLASHING_RED,
                }.get(aspect, HEAD_RED)
        elif aspect == 'SIGNAL_APPROACH_CLEAR_SIXTY':
            upper_appearance = HEAD_YELLOW
            lower_appearance = HEAD_FLASHING_GREEN
        elif aspect == 'SIGNAL_APPROACH_CLEAR_FIFTY':
            upper_appearance = HEAD_YELLOW
            lower_appearance = HEAD_GREEN
        else:
            # Same as normal signal mast, but over red (except dark).
            upper_appearance = SingleHeadMast.GetAppearance(aspect)
            if upper_appearance == HEAD_DARK:
                lower_appearance = HEAD_DARK
        
        logging.debug('  Mast %s is %s (%s over %s): %s',
            self._mast_name, aspect, upper_appearance, lower_appearance, reason)
        layout_handle.SetSignalHeadAppearance(
            self._mast_name + '_upper',
            self._upper_head_address,
            upper_appearance)
        layout_handle.SetSignalHeadAppearance(
            self._mast_name + '_lower', 
            self._lower_head_address, 
            lower_appearance)
        return SignalSummary(
            aspect,
            SignalSummary.PrettyAppearance(upper_appearance, lower_appearance),
            reason)


class SignalRoute(object):

    def __init__(self,
                next_mast_name=None,
                is_diverging=False,
                route_name=None,
                maximum_speed=None,
                dispatch_config=None):
        self._requirements = []  # list of Requirement instances
        self._is_diverging = is_diverging
        self._next_mast_name = next_mast_name # may be None
        self._route_name = route_name
        self._maximum_speed = maximum_speed
        self._dispatch_config = dispatch_config

    def AddRequirement(self, requirement):
        self._requirements.append(requirement)

    def GetAspectOrNone(self, context):
        prefix = ''
        if self._route_name:
            prefix = '[%s] ' % self._route_name

        for i, req in enumerate(self._requirements):
            if not req.IsSatisfied(context.turnout_state, context.sensor_state):
                return None, 'Unsatisfied: %s' % req
        logging.debug('  All Requirements satisfied')
        if self._next_mast_name == 'green':
            next_mast_aspect = SIGNAL_CLEAR
            logging.debug('Next mast is hard-coded as CLEAR')
        else:
            next_mast = context.masts.get(self._next_mast_name)
            if next_mast:
                next_mast_aspect, _ = next_mast.GetIntendedAspect(context)
                logging.debug('Next mast is %s, which is %s', self._next_mast_name, next_mast_aspect)
            else:
                next_mast_aspect = SIGNAL_DARK
                logging.warning('Next mast %s is unknown; assuming dark', self._next_mast_name)
        next_mast_aspect_pretty = next_mast_aspect.replace('SIGNAL_', '')
        aspect = _GetNextMostPermissiveAspect(next_mast_aspect)
        reason = 'OK to %s, which is %s' % (self._next_mast_name, next_mast_aspect_pretty)
        if self._maximum_speed == 'slow':
            # hack hack hack
            if aspect not in [SIGNAL_STOP, SIGNAL_DARK, SIGNAL_RESTRICTING]:
                aspect = SIGNAL_APPROACH
                reason = '[slow-approach] ' + reason

        # At this point, we have a non-stop aspect to return.

        # Lack of dispatch clearance should "obscure" this mast.
        if _DispatchSignalingMode(context):
            if not self._dispatch_config:
                return SIGNAL_DARK, prefix + 'No dispatch config'

            logging.debug('  Can be configured by dispatch var %s when direction is "%s"',
                          self._dispatch_config.memory_var_name, self._dispatch_config.direction)

            dispatch_section_status, invalid_dispatch_reason = _GetDispatchVarContent(context, self._dispatch_config.memory_var_name)
            if not dispatch_section_status:
                return SIGNAL_DARK, invalid_dispatch_reason

            section_status, dispatcher_label = dispatch_section_status

            direction = self._dispatch_config.direction
            clear = 'Authorized ' + direction
            occupied = 'Occupied ' + direction
            if section_status == clear:
                # TODO: Figure out if clearance is ending by looking at forward signals
                logging.info('Route %s authorized %s for %s; computing aspect',
                             self._route_name, direction, dispatcher_label)
                prefix = '[%s Authorized %s for %s] ' % (self._route_name, direction, dispatcher_label)
            elif section_status == occupied:
                return SIGNAL_RESTRICTING, '%s occupied %s' % (dispatcher_label, direction)
            else:
                return SIGNAL_STOP, 'No dispatch clearance: %s' % section_status
        
        # Now we return an aspect.
        if self._is_diverging:
            div_aspect = ConvertAspectToDivergingAspect(aspect)
            logging.debug('%s: converted aspect %s to diverging aspect %s', self._route_name, aspect, div_aspect)
            reason = prefix + 'Diverging ' + reason
            return (div_aspect, reason)
        return aspect, prefix + reason


class DispatchConfig(object):
    def __init__(self, memory_var_name, direction):
        # A system or user name of a JMRI memory
        # variable updated by the SVL Dispatcher.
        self.memory_var_name = memory_var_name
        # 'NB' or 'SB'.
        self.direction = direction


def ParseRoute(route_name, route_config):
    """Parse a route config stanza into a SignalRoute.

    Args:
        route_name: str, name for this route.
        route_config: dict, yaml config stanza for a route.

    Returns:
        SignalRoute instance.
    """
    logging.debug('Parsing route %s: %s', route_name, route_config)
    is_diverging = route_config.get('is_diverging', False)
    maximum_speed = route_config.get('maximum_speed')
    if maximum_speed:
        allowed = ['slow']
        if maximum_speed not in allowed:
            raise AttributeError('%s has invalid maximum_speed; must be one of %s' % (route_name, allowed))
    dispatch_config = None
    dispatch_config_items = route_config.get('dispatch_control')
    if dispatch_config_items:
        dispatch_config = DispatchConfig(dispatch_config_items['memory_var'],
                                         dispatch_config_items['direction'])

    route = SignalRoute(route_config.get('next_signal'), is_diverging,
                        route_name=route_name, maximum_speed=maximum_speed,
                        dispatch_config=dispatch_config)
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
            logging.debug('  Parsed turnout requrement %s %s',
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
            logging.debug('  Parsed sensor requirement %s %s',
                         sensor_name, state)

            route.AddRequirement(SensorRequirement(sensor_name, state))
        else:
            raise AttributeError(
                'Signal requirement must have turnout or sensor')
    return route

def LoadConfig(config_file_path):
    """Returns a map of {signal_name -> SignalMast}."""
    config_data = yaml.load(open(config_file_path, 'r'))
    signal_entries = {}  # name -> SignalMast
    for mast_name, configuration in config_data.items():
        logging.debug('Parsing requirements for mast %s', mast_name)

        if 'head_address' in configuration:
            head = configuration['head_address']
            logging.debug('  Mast %s configured with single head %s',
                mast_name, head)
            signal = SingleHeadMast(mast_name, head)
            del configuration['head_address']
        elif 'upper_head_address' in configuration:
            if 'lower_head_address' not in configuration:
                raise AttributeError('lower_head_address required if upper_head_address provided')
            upper = configuration['upper_head_address']
            lower = configuration['lower_head_address']
            logging.debug('  Mast %s configured with heads %s + %s', mast_name, upper, lower)
            signal = DoubleHeadMast(mast_name, upper, lower)
            del configuration['upper_head_address']
            del configuration['lower_head_address']
        else:
            raise AttributeError('Signal must define head_address or {upper,lower}_head_address')

        if 'routes' not in configuration:
            raise AttribtueError('Signal mast %s is missing a "routes" stanza' % mast_name)
        for route_name, route_info in configuration['routes'].iteritems():
            signal.AddRoute(ParseRoute(route_name, route_info))

        del configuration['routes']

        if configuration.keys():
            raise AttributeError('Unknown configuration items: %s', configuration.keys())

        signal_entries[mast_name] = signal
    return signal_entries
