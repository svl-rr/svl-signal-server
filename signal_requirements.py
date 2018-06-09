import logging

class SignalRequirement(object):

    # TODO: should probs return most permissive aspect.
	def IsSatisfied(self, turnouts, sensors):
		raise NotImplementedError('Not implemented')

class SensorRequirement(SignalRequirement):

	def __init__(self, sensor_name, required_state):
		self._sensor_name = sensor_name
		self._required_state = required_state

	def __str__(self):
		return 'SensorRequirement{%s=%s}' % (self._sensor_name, self._required_state)

	def IsSatisfied(self, turnouts, sensors):
		actual_sensor_state = sensors.get(self._sensor_name)
		if not actual_sensor_state:
			logging.error(
				'Required sensor %s not found in provided data', self._sensor_name)
			return False
		if actual_sensor_state == self._required_state:
			logging.debug('%s satisfied', self)
			return True
		logging.debug('%s not satisfied (actual state: %s)',
					  self, actual_sensor_state)
		return False

class TurnoutRequirement(SignalRequirement):

	def __init__(self, turnout_name, required_state):
		self._turnout_name = turnout_name
		self._required_state = required_state

	def __str__(self):
		return 'TurnoutRequirement{%s=%s}' % (self._turnout_name, self._required_state)

	def IsSatisfied(self, turnouts, sensors):
		actual_turnout_state = turnouts.get(self._turnout_name)
		if not actual_turnout_state:
			logging.error(
				'Required turnout %s not found in provided data', self._turnout_name)
			return False
		if actual_turnout_state == self._required_state:
			logging.debug('%s satisfied', self)
			return True
		logging.debug('%s not satisfied (actual state: %s)',
					  self, actual_turnout_state)
		return False