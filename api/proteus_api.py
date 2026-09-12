"""Native Proteus project editing and Windows simulation control."""
from proteus_project import Project, UnsupportedFormat
from proteus_session import Session, parse_sdf
from component_codec import Circuit
from device_library import Library
from net_objects import decode_net_objects
from simulation import Simulation, firmware_info, extract_firmware, gpio_events
from measurements import (graphs, export_graph, parse_graph_csv, sample as sample_graph,
                          generators, set_generator_properties)
from clone_research import clone_experiment as clone_rescap_capacitor
from connectivity_research import swap_inputs_demo as swap_comb01_inputs

__version__ = '0.2.0'
__all__ = ['Circuit', 'Project', 'Library', 'Session', 'Simulation', 'UnsupportedFormat',
           'parse_sdf', 'decode_net_objects', 'firmware_info', 'extract_firmware', 'gpio_events',
           'graphs', 'export_graph', 'parse_graph_csv', 'sample_graph', 'generators', 'set_generator_properties',
           'clone_rescap_capacitor', 'swap_comb01_inputs']
