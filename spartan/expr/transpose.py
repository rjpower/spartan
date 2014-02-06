'''
Transpose operation and expr.
'''

import numpy as np
import scipy.sparse as sp
from spartan import rpc
from .base import Expr, lazify
from .. import blob_ctx, util
from ..node import Node, node_type
from ..util import is_iterable, Assert
from ..array import extent, tile, distarray
from .map import MapResult

def _transpose_mapper(array, ex, _dest_shape):
  tile = array.fetch(ex)
  target_ex = extent.create(ex.ul[::-1], ex.lr[::-1], _dest_shape)
  if not array.sparse:
    yield target_ex, np.transpose(tile)
  else:
    yield target_ex, tile.transpose()

def _target_mapper(ex, map_fn=None, inputs=None, target=None, fn_kw=None):
  result = list(map_fn(inputs, ex, **fn_kw))

  futures = rpc.FutureGroup()
  if result is not None:
    for ex, v in result:
      futures.append(target.update(ex, v, wait=False))

  return MapResult(None, futures)

@node_type
class TransposeExpr(Expr):
  _members = ['array', 'tile_hint']

  def __str__(self):
    return 'Transpose[%d] %s' % (self.expr_id, self.expr)

  def _evaluate(self, ctx, deps):
    v = deps['array']
    shape = v.shape[::-1]
    fn_kw = {'_dest_shape' : shape}

    target = distarray.create(shape, dtype = v.dtype, sparse = v.sparse)
    v.foreach_tile(mapper_fn = _target_mapper,
                   kw = {'map_fn':_transpose_mapper, 'inputs':v,
                         'target':target, 'fn_kw':fn_kw})
    return target


def transpose(array, tile_hint = None):
  '''
  Transpose ``array``.

  Return a TransposeExpr.

  :param array: `Expr`
  '''

  array = lazify(array)

  return TransposeExpr(array = array, tile_hint = tile_hint)

