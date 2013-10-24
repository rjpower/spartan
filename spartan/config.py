#!/usr/bin/env python

"""
Configuration options and flags.

"""

import argparse
import time

parser = argparse.ArgumentParser()
_names = set() 

def add_flag(name, *args, **kw):
  _names.add(kw.get('dest', name))
  
  parser.add_argument('--' + name, *args, **kw)
  if 'default' in kw:
    return kw['default']
  return None
  
def add_bool_flag(name, default, **kw):
  _names.add(name)
  
  parser.add_argument('--' + name, default=default, type=int, dest=name, **kw)
  parser.add_argument('--enable_' + name, action='store_true', dest=name)
  parser.add_argument('--disable_' + name, action='store_false', dest=name)
  
  return default

class AssignMode(object):
  BY_CORE = 1
  BY_NODE = 2

class Flags(object):
  _parsed = False
  
  profile_kernels = add_bool_flag('profile_kernels', default=False)
  profile_master = add_bool_flag('profile_master', default=False)
  log_level = add_flag('log_level', default=3, type=int)
  num_workers = add_flag('num_workers', default=1, type=int)
  cluster = add_bool_flag('cluster', default=False)
  oprofile = add_bool_flag('oprofile', default=False)
  
  port_base = add_flag('port_base', type=int, default=10000,
    help='Port to listen on (master = port_base, workers=port_base + N)')
  
  use_threads = add_bool_flag(
    'use_threads',
    help='When running locally, use threads instead of forking. (slow, for debugging)', 
    default=True)
  
  assign_mode = AssignMode.BY_NODE
  add_flag('bycore', dest='assign_mode', action='store_const', const=AssignMode.BY_CORE)
  add_flag('bynode', dest='assign_mode', action='store_const', const=AssignMode.BY_NODE)
  
  def __getattr__(self, name):
    assert self.__dict__['_parsed'], 'Flags accessed before parse_args called.'
    return self.__dict__[name]
  
  def __repr__(self):
    result = []
    for k, v in iter(self):
      result.append('%s : %s' % (k, v))
    return '\n'.join(result)
  
  def __iter__(self):
    return iter([(k, getattr(self, k)) for k in dir(self)
                 if not k.startswith('_')])

flags = Flags()

def parse_args(argv):
  parsed_flags, rest = parser.parse_known_args(argv)
  for flagname in _names:
    setattr(flags, flagname, getattr(parsed_flags, flagname))
 
  flags._parsed = True
  return rest

#HOSTS = [ ('localhost', 8) ]

HOSTS = [
  ('beaker-20', 8),
  #('beaker-21', 8),
  ('beaker-22', 8),
  ('beaker-23', 8),
  ('beaker-24', 8),
  ('beaker-25', 8),
  ('beaker-14', 7),
  ('beaker-15', 7),
  ('beaker-16', 8),
  ('beaker-17', 7),
  ('beaker-18', 4),
  ('beaker-19', 7),
]
