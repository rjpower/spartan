'''
Basic numpy style operations on arrays.

These include --

* Array creation routines: (`rand`, `randn`, `zeros`, `ones`, `arange`)
* Reductions: (`sum`, `argmin`, `argmax`, `mean`)
* Shape/type casting: (`reshape`, `ravel`, `astype`, `shape`, `size`)
* Other: (`dot`).
'''
import sys

import numpy as np
import scipy.sparse as sp

from .. import util
from ..array import distarray, extent
from ..array.extent import index_for_reduction, shapes_match
from ..util import Assert
from .base import force
from .loop import loop
from .map import map
from .ndarray import ndarray
from .optimize import disable_parakeet, not_idempotent
from .reduce import reduce
from .shuffle import shuffle
from spartan import sparse

def _make_ones(input): return np.ones(input.shape, input.dtype)
def _make_zeros(input): return np.zeros(input.shape, input.dtype)

@not_idempotent
@disable_parakeet
def _make_rand(input):
  return np.random.rand(*input.shape)

@not_idempotent
@disable_parakeet
def _make_randn(input):
  return np.random.randn(*input.shape)

@not_idempotent
@disable_parakeet
def _make_sparse_rand(input, 
                      density=None, 
                      dtype=None, 
                      format='csr'):
  Assert.eq(len(input.shape), 2)
  
  return sp.rand(input.shape[0],
                 input.shape[1],
                 density=density,
                 format=format,
                 dtype=dtype)

@not_idempotent
def _make_sparse_diagonal(tile, ex):
  data = sp.lil_matrix(ex.shape)

  if ex.ul[0] >= ex.ul[1] and ex.ul[0] < ex.lr[1]:
    for i in range(ex.ul[0], min(ex.lr[0], ex.lr[1])):
      data[i - ex.ul[0], i - ex.ul[1]] = 1
  elif ex.ul[1] >= ex.ul[0] and ex.ul[1] < ex.lr[0]:
    for j in range(ex.ul[1], min(ex.lr[1], ex.lr[0])):
      data[j - ex.ul[0], j - ex.ul[1]] = 1

  return [(ex, data)]
  
def rand(*shape, **kw):
  '''
  :param tile_hint: A tuple indicating the desired tile shape for this array.
  '''
  tile_hint = None
  if 'tile_hint' in kw:
    tile_hint = kw['tile_hint']
    del kw['tile_hint']

  assert len(kw) == 0, 'Unknown keywords %s' % kw

  for s in shape: assert isinstance(s, int)
  return map(ndarray(shape, dtype=np.float, tile_hint=tile_hint),
             fn=_make_rand)


def randn(*shape, **kw):
  tile_hint = None
  if 'tile_hint' in kw:
    tile_hint = kw['tile_hint']
    del kw['tile_hint']
  
  for s in shape: assert isinstance(s, int)
  return map(ndarray(shape, dtype=np.float, tile_hint=tile_hint), fn=_make_randn) 


def sparse_rand(shape, 
                density=0.001,
                format='lil',
                dtype=np.float32, 
                tile_hint=None):
  for s in shape: assert isinstance(s, int)
  return map(ndarray(shape, dtype=dtype, tile_hint=tile_hint, sparse=True),
             fn=_make_sparse_rand,
             fn_kw = { 'dtype' : dtype, 
                       'density' : density,
                       'format' : format })

def sparse_empty(shape,
                 dtype=np.float32,
                 tile_hint=None):
    return ndarray(shape, dtype=dtype, tile_hint=tile_hint, sparse=True)

def sparse_diagonal(shape,
                    dtype=np.float32,
                    tile_hint=None):
  return shuffle(ndarray(shape, dtype=dtype, tile_hint=tile_hint, sparse=True), _make_sparse_diagonal)

@disable_parakeet 
def _tocoo(data):
  return data.tocoo()

def tocoo(array):
  return map(array, fn=_tocoo)


def zeros(shape, dtype=np.float, tile_hint=None):
  return map(ndarray(shape, dtype=dtype, tile_hint=tile_hint),
             fn=_make_zeros)


def ones(shape, dtype=np.float, tile_hint=None):
  return map(ndarray(shape, dtype=dtype, tile_hint=tile_hint),
             fn=_make_ones)



def _arange_mapper(inputs, ex, dtype=None):
  pos = extent.ravelled_pos(ex.ul, ex.array_shape)
  #util.log_info('Extent: %s, shape:%s, pos: %s', ex, ex.shape, pos)
  sz = np.prod(ex.shape)
  yield (ex, np.arange(pos, pos + sz, dtype=dtype).reshape(ex.shape))


def arange(shape, dtype=np.float, tile_hint=None):
  return shuffle(ndarray(shape, dtype=dtype, tile_hint=tile_hint),
                 fn=_arange_mapper,
                 kw={'dtype': dtype})


def _sum_local(ex, data, axis):
  #util.log_info('Summing: %s %s', ex, axis)
  #util.log_info('Summing: %s', data.shape)
  #util.log_info('Result: %s', data.sum(axis).shape)
  return data.sum(axis)


def sum(x, axis=None):
  '''
  Sum ``x`` over ``axis``.
  
  
  :param x: The array to sum.
  :param axis: Either an integer or ``None``.
  '''
  return reduce(x,
                axis=axis,
                dtype_fn=lambda input: input.dtype,
                local_reduce_fn=_sum_local,
                accumulate_fn=np.add)

def _scan_reduce_mapper(array, ex, reduce_fn=None, axis=None):
  if reduce_fn is None:
    yield (ex, array.fetch(ex))
  else:  
    local_reduction = reduce_fn(array.fetch(ex), axis=axis)
    if axis is None:
      exts = sorted(array.tiles.keys(), key=lambda x: x.ul)
      id = exts.index(ex)
      dst_ex = extent.create((id,),(id+1,),(len(exts),))
    else:
      max_axis_shape = max([ext.shape[axis] for ext in array.tiles.keys()])  
      id = ex.ul[axis] / max_axis_shape
      new_ul = list(ex.ul)
      new_lr = list(ex.lr)
      new_shape = list(ex.array_shape)
      new_ul[axis] = id
      new_lr[axis] = id + 1
      new_shape[axis] = int(np.ceil(array.shape[axis] * 1.0 / max_axis_shape))
    
      dst_ex = extent.create(new_ul, new_lr, new_shape)
      
    local_reduction = np.asarray(local_reduction).reshape(dst_ex.shape)
    #util.log_info('2 orig_ex:%s dst_ex:%s local_reduction:%s shape:%s', ex, dst_ex, local_reduction, local_reduction.shape)
    yield (dst_ex, local_reduction)

def _scan_mapper(array, ex, scan_fn=None, axis=None, scan_base=None):
  if scan_fn is None:
    yield (ex, array.fetch(ex))
  else:
    local_data = array.fetch(ex)
    if sp.issparse(local_data):
      local_data = local_data.todense()
      
    if axis is None:
      exts = sorted(array.tiles.keys(), key=lambda x: x.ul)
      id = exts.index(ex)
      if id > 0:
        local_data[tuple(np.zeros(len(ex.shape)))] += scan_base[id-1]

    else:
      max_axis_shape = max([ext.shape[axis] for ext in array.tiles.keys()])  
      id = ex.ul[axis] / max_axis_shape
      if id > 0:
        base_slice = list(ex.to_slice())
        base_slice[axis] = slice(id-1, id, None)
        new_slice = [slice(0, ex.shape[i], None) for i in range(len(ex.shape))]
        new_slice[axis] = slice(0,1,None)
        local_data[new_slice] += scan_base[base_slice]
    
    #util.log_info('local_data type:%s data:%s', type(local_data), local_data)
        
    yield (ex, np.asarray(scan_fn(local_data, axis=axis)).reshape(ex.shape))   
      
def scan(array, reduce_fn=None, scan_fn=None, accum_fn=None, axis=None):
  '''
  Scan ``array`` over ``axis``.
  
  
  :param array: The array to scan.
  :param reduce_fn: local reduce function
  :param scan_fn: scan function
  :param accum_fn: accumulate function
  :param axis: Either an integer or ``None``.
  '''
  reduce_result = shuffle(array, fn=_scan_reduce_mapper, kw={'axis': axis,
                                                             'reduce_fn': reduce_fn})
  fetch_result = reduce_result.glom()
  if scan_fn is not None:
    fetch_result = scan_fn(fetch_result, axis=axis)
  
  scan_result = shuffle(array, fn=_scan_mapper, kw={'scan_fn':scan_fn,
                                                    'axis': axis,
                                                    'scan_base':fetch_result})
  return scan_result

def mean(x, axis=None):
  '''
  Compute the mean of ``x`` over ``axis``.
  
  See `numpy.ndarray.mean`.
  
  :param x: `Expr`
  :param axis: integer or ``None``
  '''
  if axis is None:
    return sum(x, axis) / np.prod(x.shape)
  else:
    return sum(x, axis) / x.shape[axis]


def _to_structured_array(*vals):
  '''Create a structured array from the given input arrays.
  
  :param vals: A list of (field_name, `np.ndarray`)
  :rtype: A structured array with fields from ``kw``.
  '''
  out = np.ndarray(vals[0][1].shape,
                   dtype=','.join([a.dtype.str for name, a in vals]))
  out.dtype.names = [name for name, a in vals]
  for k, v in vals:
    out[k] = v
  return out


@disable_parakeet
def _take_idx_mapper(input):
  return input['idx']


def _dual_reducer(ex, tile, axis, idx_f=None, val_f=None):
  Assert.isinstance(ex, extent.TileExtent)
  local_idx = idx_f(tile[:], axis)
  local_val = val_f(tile[:], axis)

  global_idx = ex.to_global(local_idx, axis)
  new_idx = index_for_reduction(ex, axis)
  new_val = _to_structured_array(('idx', global_idx), ('val', local_val))

  assert shapes_match(new_idx, new_val), (new_idx, new_val.shape)
  return new_val


def _dual_combiner(a, b, op):
  return np.where(op(a['val'], b['val']), a, b)


def _dual_dtype(input):
  dtype = np.dtype('i8,%s' % input.dtype.str)
  dtype.names = ('idx', 'val')
  return dtype


def argmin(x, axis=None):
  '''
  Compute argmin over ``axis``.
  
  See `numpy.ndarray.argmin`.
  
  :param x: `Expr` to compute a minimum over. 
  :param axis: Axis (integer or None).
  '''
  compute_min = reduce(x, axis,
                       dtype_fn=_dual_dtype,
                       local_reduce_fn=_dual_reducer,
                       accumulate_fn=lambda a, b: _dual_combiner(a, b, np.less),
                       fn_kw={'idx_f': np.argmin, 'val_f': np.min})

  take_indices = map(compute_min, _take_idx_mapper)
  return take_indices


def argmax(x, axis=None):
  '''
  Compute argmax over ``axis``.
  
  See `numpy.ndarray.argmax`.
  
  :param x: `Expr` to compute a maximum over. 
  :param axis: Axis (integer or None).
  '''
  compute_max = reduce(x, axis,
                       dtype_fn=_dual_dtype,
                       local_reduce_fn=_dual_reducer,
                       accumulate_fn=lambda a, b: _dual_combiner(a, b, np.greater),
                       fn_kw={'idx_f': np.argmax, 'val_f': np.max})

  take_indices = map(compute_max, _take_idx_mapper)

  
  return take_indices

def _countnonzero_local(ex, data, axis):
  return np.asarray(np.count_nonzero(data))

def count_nonzero(array):
  return reduce(array, None,
                dtype_fn=lambda input: np.int64,
                local_reduce_fn=_countnonzero_local,
                accumulate_fn = np.add)

def _countzero_local(ex, data, axis):
  return np.asarray(np.prod(ex.shape) - np.count_nonzero(data))

def count_zero(array):
  return reduce(array, None,
                dtype_fn=lambda input: np.int64,
                local_reduce_fn=_countzero_local,
                accumulate_fn = np.add)
 

def size(x, axis=None):
  '''
  Return the size (product of the size of all axes) of ``x``.
  
  See `numpy.ndarray.size`.
  
  :param x: `Expr` to compute the size of.
  '''
  if axis is None:
    return np.prod(x.shape)
  return x.shape[axis]

@disable_parakeet
def _astype_mapper(t, dtype):
  return t.astype(dtype)

def astype(x, dtype):
  '''
  Convert ``x`` to a new dtype.
  
  See `numpy.ndarray.astype`.
  
  :param x:
  :param dtype:
  '''
  assert x is not None
  return map(x, _astype_mapper, fn_kw={'dtype': np.dtype(dtype).str })


def _ravel_mapper(array, ex):
  ul = extent.ravelled_pos(ex.ul, ex.array_shape)
  lr = 1 + extent.ravelled_pos([lr - 1 for lr in ex.lr], ex.array_shape)
  shape = (np.prod(ex.array_shape),)

  ravelled_ex = extent.create((ul,), (lr,), shape)
  ravelled_data = array.fetch(ex).ravel()
  yield ravelled_ex, ravelled_data


def ravel(v):
  '''
  "Ravel" ``v`` to a one-dimensional array of shape (size(v),).
  
  See `numpy.ndarray.ravel`.
  :param v:
  '''
  return shuffle(v, _ravel_mapper)


def _dot_mapper(inputs, ex, av, bv):
  # read current tile of array 'a'
  ex_a = ex

  # fetch all column tiles of b that correspond to tile a's rows, i.e.
  # rows = ex_a.cols (should be ex_a.rows?)
  # cols = *
  ex_b = extent.create((ex_a.ul[1], 0),
                       (ex_a.lr[1], bv.shape[1]),
                       bv.shape)

  time_a, a = util.timeit(lambda: av.fetch(ex_a))
  #util.log_info('Fetched...%s in %s', ex_a, time_a)
  time_b, b = util.timeit(lambda: bv.fetch(ex_b))
  util.log_debug('Fetched...ax:%s in %s, bx:%s in %s', ex_a, time_a, ex_b, time_b)

  #util.log_info('%s %s %s', type(a), a.shape, a.dtype)
  #util.log_info('%s %s %s', type(b), b.shape, b.dtype)
  # util.log_info('%s %s', bv.shape, len(bv.tiles))
  
  #util.log_info('%s %s', type(a), type(b))
  
  #if not sp.issparse(a):
  #  a = sp.csr_matrix(a)
  #if not sp.issparse(b):
  #  b = sp.csr_matrix(b)
  
  #result = a.dot(b)
  
  if isinstance(a, sp.coo_matrix) and b.shape[1] == 1:
    result = sparse.dot_coo_dense_unordered_map(a, b)
  else:
    result = a.dot(b)
  
  ul = np.asarray([ex_a.ul[0], 0])
  lr = ul + result.shape
  target_shape = (av.shape[0], bv.shape[1])
  target_ex = extent.create(ul, lr, target_shape)

  # util.log_info('A: %s', a.dtype)
  # util.log_info('B: %s', b.dtype)
  # util.log_info('R: %s', result.dtype)
  # util.log_info('T: %s', target_ex)

  # util.log_info('%s %s %s', a.shape, b.shape, result.shape)
  # util.log_info('%s %s %s', ul, lr, target_shape)
  yield target_ex, result


def _dot_numpy(array, ex, numpy_data=None):
  l = array.fetch(ex)
  r = numpy_data

  yield (ex[0].add_dim(), np.dot(l, r))

def dot(a, b):
  '''
  Compute the dot product (matrix multiplication) of 2 arrays.
  
  :param a: `Expr` or `numpy.ndarray` 
  :param b: `Expr` or `numpy.ndarray`
  :rtype: `Expr`
  '''
  av = force(a)
  bv = force(b)

  if isinstance(bv, np.ndarray):
    return shuffle(av, _dot_numpy, kw={'numpy_data': bv})

  # av, bv = distarray.broadcast([av, bv])
  Assert.eq(a.shape[1], b.shape[0])
  sparse = av.sparse and bv.sparse
  target = ndarray((a.shape[0], b.shape[1]),
                   dtype=av.dtype,
                   tile_hint=np.maximum(av.tile_shape(), bv.tile_shape()),
                   reduce_fn=np.add,
                   sparse=sparse)
  
  return shuffle(av, _dot_mapper, target=target, kw=dict(av=av, bv=bv))
    
def op_map(*args, **kw):
  fn = kw['fn']
  return map(args, fn)
 
def add(a, b):
  return op_map(a, b, fn=lambda a, b: a + b)

def sub(a, b):
  return op_map(a, b, fn=lambda a, b: a - b)
  
def ln(v): return map(v, fn=np.log)


def log(v): return map(v, fn=np.log)


def exp(v): return map(v, fn=np.exp)


def sqrt(v): return map(v, fn=np.sqrt)


def abs(v): return map(v, fn=np.abs)

try:
  import scipy.stats

  def norm_cdf(v):
    return map(v, fn=scipy.stats.norm.cdf, numpy_expr='mathlib.norm_cdf')
except:
  print >>sys.stderr, 'Missing scipy.stats (some functions will be unavailable.'
