import os
import six
import time
import unittest

import tensorflow as tf
import numpy as np

from opennmt.utils import checkpoint as checkpoint_util


class _DummyModel(tf.keras.layers.Layer):

  def __init__(self):
    super(_DummyModel, self).__init__()
    self.layers = [tf.keras.layers.Dense(20), tf.keras.layers.Dense(20)]

  def call(self, x):
    for layer in self.layers:
      x = layer(x)
    return x


class CheckpointTest(tf.test.TestCase):

  def _generateCheckpoint(self,
                          model_dir,
                          step,
                          variables,
                          last_checkpoints=None,
                          prefix="model.ckpt"):
    with tf.Graph().as_default() as graph:
      for name, value in six.iteritems(variables):
        if isinstance(value, tuple):
          dtype = None
          initializer = tf.random_uniform_initializer()
          shape = value
        else:
          dtype = tf.as_dtype(value.dtype)
          initializer = tf.constant_initializer(value, dtype=dtype)
          shape = value.shape
        _ = tf.get_variable(
            name,
            shape=shape,
            dtype=dtype,
            initializer=initializer)
      global_step = tf.get_variable(
          "global_step",
          initializer=tf.constant(step, dtype=tf.int64),
          trainable=False)
      saver = tf.train.Saver(tf.global_variables())
      if last_checkpoints:
        saver.set_last_checkpoints_with_time(last_checkpoints)
      with self.test_session(graph=graph) as sess:
        sess.run(tf.global_variables_initializer())
        saver.save(sess, os.path.join(model_dir, prefix), global_step=global_step)
      return saver.last_checkpoints[0], time.time()

  def testCheckpointAveraging(self):
    model = _DummyModel()
    optimizer = tf.keras.optimizers.Adam()

    @tf.function
    def _build_model():
      x = tf.random.uniform([4, 10])
      y = model(x)
      loss = tf.reduce_mean(y)
      gradients = optimizer.get_gradients(loss, model.trainable_variables)
      optimizer.apply_gradients(zip(gradients, model.trainable_variables))

    def _assign_var(var, scalar):
      var.assign(tf.ones_like(var) * scalar)

    def _all_equal(var, scalar):
      return tf.size(tf.where(tf.not_equal(var, scalar))).numpy() == 0

    def _get_var_list(checkpoint_path):
      return [name for name, _ in tf.train.list_variables(checkpoint_path)]

    _build_model()

    # Write some checkpoint with all variables set to the step value.
    steps = [10, 20, 30, 40]
    num_checkpoints = len(steps)
    avg_value = sum(steps) / num_checkpoints
    directory = os.path.join(self.get_temp_dir(), "src")
    checkpoint = tf.train.Checkpoint(model=model, optimizer=optimizer)
    checkpoint_manager = tf.train.CheckpointManager(
        checkpoint, directory, max_to_keep=num_checkpoints)
    for step in steps:
      _assign_var(model.layers[0].kernel, step)
      _assign_var(model.layers[0].bias, step)
      checkpoint_manager.save(checkpoint_number=step)

    output_dir = os.path.join(self.get_temp_dir(), "dst")
    checkpoint_util.average_checkpoints(
        directory, output_dir, dict(model=model, optimizer=optimizer))
    avg_checkpoint = tf.train.latest_checkpoint(output_dir)
    self.assertIsNotNone(avg_checkpoint)
    checkpoint.restore(avg_checkpoint)
    self.assertTrue(_all_equal(model.layers[0].kernel, avg_value))
    self.assertTrue(_all_equal(model.layers[0].bias, avg_value))
    self.assertListEqual(
        _get_var_list(avg_checkpoint),
        _get_var_list(checkpoint_manager.latest_checkpoint))

  @unittest.skip
  def testCheckpointDTypeConversion(self):
    model_dir = os.path.join(self.get_temp_dir(), "ckpt-fp32")
    os.makedirs(model_dir)
    variables = {
      "x": np.ones((2, 3), dtype=np.float32),
      "optim/x": np.ones((2, 3), dtype=np.float32),
      "counter": np.int64(42)
    }
    checkpoint_path, _ = self._generateCheckpoint(model_dir, 10, variables)
    half_dir = os.path.join(model_dir, "fp16")
    checkpoint.convert_checkpoint(checkpoint_path, half_dir, tf.float32, tf.float16)
    half_var = checkpoint.get_checkpoint_variables(half_dir)
    self.assertEqual(half_var["global_step"], 10)
    self.assertEqual(half_var["x"].dtype, np.float16)
    self.assertEqual(half_var["optim/x"].dtype, np.float32)
    self.assertEqual(half_var["counter"].dtype, np.int64)


if __name__ == "__main__":
  tf.test.main()
