import unittest

import numpy as np
from spartan import expr, util
from spartan.util import Assert
from test_common import with_ctx
import test_common


TEST_SIZE = 10
class TestMapTiles(test_common.ClusterTest):
  def test_map_1(self):
    a = expr.ones((20, 20))
    b = expr.ones((20, 20))
    c = a + b

    Assert.all_eq(2 * np.ones((20, 20)), expr.glom(c))

  def test_ln(self):
    a = 1.0 + expr.ones((100,), dtype=np.float32)
    b = 1.0 + np.ones(100).astype(np.float32)

    Assert.all_close(expr.ln(a).glom(), np.log(b))

  def test_add2(self):
    a = expr.ones((TEST_SIZE, TEST_SIZE))
    b = expr.ones((TEST_SIZE, TEST_SIZE))
    Assert.all_eq((a + b).glom(), np.ones((TEST_SIZE, TEST_SIZE)) * 2)

  def test_add3(self):
    a = expr.ones((TEST_SIZE, TEST_SIZE))
    b = expr.ones((TEST_SIZE, TEST_SIZE))
    c = expr.ones((TEST_SIZE, TEST_SIZE))
    Assert.all_eq((a + b + c).glom(), np.ones((TEST_SIZE, TEST_SIZE)) * 3)


  def test_add_many(self):
    a = expr.ones((TEST_SIZE, TEST_SIZE))
    b = expr.ones((TEST_SIZE, TEST_SIZE))

    add_many = (a + b + a + b + a + b + a + b + a + b)
    #print add_many
    #print add_many.dag()
    Assert.all_eq(add_many.glom(),
                  np.ones((TEST_SIZE, TEST_SIZE)) * 10)

  def test_index(self):
    a = expr.arange((TEST_SIZE, TEST_SIZE))
    b = expr.ones((10,), dtype=np.int)
    z = a[b]
    val = expr.evaluate(z)

    nx = np.arange(TEST_SIZE * TEST_SIZE).reshape(TEST_SIZE, TEST_SIZE)
    ny = np.ones((10,), dtype=np.int)

    Assert.all_eq(val.glom(), nx[ny])
    
  def test_broadcast(self):
    a = expr.ones((2, 1))
    b = expr.ones((2, 5))
    Assert.all_eq((a / b).glom(), np.ones((2, 5)))
    Assert.all_eq((b / a).glom(), np.ones((2, 5)))

if __name__ == '__main__':
  unittest.main()
