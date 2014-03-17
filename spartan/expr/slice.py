from .. import util, node, core
from ..util import Assert
from ..array import distarray, extent
from . import base


def _slice_mapper(ex, **kw):
  '''
  Run when mapping over a slice.
  Computes the intersection of the current tile and a global slice.
  If the slice is non-zero, then run the user mapper function.
  Otherwise, do nothing.

  :param ex:
  :param tile:
  :param mapper_fn: User mapper function.
  :param slice: `TileExtent` representing the slice of the input array.
  '''

  mapper_fn = kw['_slice_fn']
  slice_extent = kw['_slice_extent']

  fn_kw = kw['fn_kw']
  if fn_kw is None: fn_kw = {}

  intersection = extent.intersection(slice_extent, ex)
  if intersection is None:
    return core.LocalKernelResult(result=[])

  offset = extent.offset_from(slice_extent, intersection)
  offset.array_shape = slice_extent.shape

  subslice = extent.offset_slice(ex, intersection)

  result = mapper_fn(offset, **fn_kw)
  #util.log_info('Slice mapper[%s] %s %s -> %s', mapper_fn, offset, subslice, result)
  return result

class Slice(distarray.DistArray):
  '''
  Represents a Numpy multi-dimensional slice on a base `DistArray`.

  Slices in Spartan do not result in a copy.  A `Slice` object is
  returned instead.  Slice objects support mapping (``foreach_tile``)
  and fetch operations.
  '''
  def __init__(self, darray, idx):
    if not isinstance(idx, extent.TileExtent):
      idx = extent.from_slice(idx, darray.shape)
    util.log_info('New slice: %s', idx)

    Assert.isinstance(darray, distarray.DistArray)
    self.darray = darray
    self.slice = idx
    self.shape = self.slice.shape
    intersections = [extent.intersection(self.slice, ex) for ex in self.darray.tiles]
    intersections = [ex for ex in intersections if ex is not None]
    offsets = [extent.offset_from(self.slice, ex) for ex in intersections]
    self.tiles = offsets
    self.dtype = darray.dtype

  @property
  def bad_tiles(self):
    bad_intersections = [extent.intersection(self.slice, ex) for ex in self.darray.bad_tiles]
    return [ex for ex in bad_intersections if ex is not None]

  def foreach_tile(self, mapper_fn, kw):
    return self.darray.foreach_tile(mapper_fn = _slice_mapper,
                                    kw={'fn_kw' : kw,
                                        '_slice_extent' : self.slice,
                                        '_slice_fn' : mapper_fn })

  def fetch(self, idx):
    offset = extent.compute_slice(self.slice, idx.to_slice())
    return self.darray.fetch(offset)


@node.node_type
class SliceExpr(base.Expr):
  '''Represents an indexing operation.

  Attributes:
    src: `Expr` to index into
    idx: `tuple` (for slicing) or `Expr` (for bool/integer indexing)
    broadcast_to: shape to broadcast to before slicing
  '''
  _members = ['src', 'idx', 'broadcast_to']

  def node_init(self):
    base.Expr.node_init(self)
    assert not isinstance(self.src, base.ListExpr)
    assert not isinstance(self.idx, base.ListExpr)
    assert not isinstance(self.idx, base.TupleExpr)

  def label(self):
    return 'slice(%s)' % self.idx

  def compute_shape(self):
    if isinstance(self.idx, (int, slice, tuple)):
      src_shape = self.src.compute_shape()
      ex = extent.from_shape(src_shape)
      slice_ex = extent.compute_slice(ex, self.idx)
      return slice_ex.shape
    else:
      raise base.NotShapeable

  def _evaluate(self, ctx, deps):
    '''
    Index an array by a slice.

    Args:
      ctx: `BlobCtx`
      src: `DistArray` to read from
      idx: int or tuple

    Returns:
      Slice: The result of src[idx]
    '''
    src = deps['src']
    idx = deps['idx']

    assert not isinstance(idx, list)
    util.log_debug('Evaluating slice: %s', idx)
    return Slice(src, idx)