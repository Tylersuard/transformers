# Copyright 2022 The HuggingFace Team. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from typing import List, Optional, Union

import numpy as np
import tensorflow as tf

from .utils import logging


logger = logging.get_logger(__name__)


def shape_list(tensor: Union[tf.Tensor, np.ndarray]) -> List[int]:
    """
    Deal with dynamic shape in tensorflow cleanly.

    Args:
        tensor (`tf.Tensor` or `np.ndarray`): The tensor we want the shape of.

    Returns:
        `List[int]`: The shape of the tensor as a list.
    """
    if isinstance(tensor, np.ndarray):
        return list(tensor.shape)

    dynamic = tf.shape(tensor)

    if tensor.shape == tf.TensorShape(None):
        return dynamic

    static = tensor.shape.as_list()

    return [dynamic[i] if s is None else s for i, s in enumerate(static)]


def stable_softmax(logits: tf.Tensor, axis: Optional[int] = None, name: Optional[str] = None) -> tf.Tensor:
    """
    Stable wrapper that returns the same output as `tf.nn.softmax`, but that works reliably with XLA on CPU. It is
    meant as a workaround for the [following issue](https://github.com/tensorflow/tensorflow/issues/55682), and will be
    removed after it gets fixed. The arguments and outputs are the same as `tf.nn.softmax`, and relies on the fact that
    `softmax(x) = softmax(x + c)` (see https://ogunlao.github.io/2020/04/26/you_dont_really_know_softmax.html).

    Args:
        logits (`tf.Tensor`):
            Must be one of the following types: half, float32, float64.
        axis (`int`, *optional*):
            The dimension softmax would be performed on. The default is -1 which indicates the last dimension.
        name (`str`, *optional*):
            A name for the operation.

    Returns:
        `tf.Tensor`:
            A Tensor. Has the same type and shape as logits.
    """
    # TODO: When the issue linked above gets sorted, add a check on TF version here and use the original function if
    # it has the fix. After we drop the support for unfixed versions, remove this function.
    return tf.nn.softmax(logits=logits + 1e-9, axis=axis, name=name)


def functional_layernorm(inputs, weight, bias, epsilon=1e-5, axis=-1):
    # This is a very simplified functional layernorm, designed to duplicate
    # the functionality of PyTorch nn.functional.layer_norm when this is needed to port
    # models in Transformers.

    if weight.shape.rank != 1 or bias.shape.rank != 1 or not isinstance(axis, int):
        raise NotImplementedError("Only 1D weight and bias tensors are supported for now, with only a single axis.")

    # Calculate the moments on the last axis (layer activations).
    mean, variance = tf.nn.moments(inputs, axes=[axis], keepdims=True)

    if axis != -1:
        # Reshape scale and weight to have the same rank as inputs, but with 1 dimensions
        # on every dimension except axis
        shape = [1] * inputs.shape.rank
        shape[axis] = shape_list(inputs)[axis]
        weight = tf.reshape(weight, shape)
        bias = tf.reshape(bias, shape)

    # Compute layer normalization using the batch_normalization
    # function.
    outputs = tf.nn.batch_normalization(
        inputs,
        mean,
        variance,
        offset=bias,
        scale=weight,
        variance_epsilon=epsilon,
    )
    return outputs


def flatten(input, start_dim=0, end_dim=-1):
    # Replicates the behavior of torch.flatten in TF

    # If end_dim or start_dim is negative, count them from the end
    if end_dim < 0:
        end_dim += input.shape.rank
    if start_dim < 0:
        start_dim += input.shape.rank

    if start_dim == end_dim:
        return input

    in_shape = tf.shape(input)
    flattened_dim = tf.math.reduce_prod(in_shape[start_dim : end_dim + 1])
    out_shape = tf.concat([in_shape[:start_dim], [flattened_dim], in_shape[end_dim + 1 :]], axis=0)
    return tf.reshape(input, out_shape)


def invert_attention_mask(encoder_attention_mask: tf.Tensor) -> tf.Tensor:
    """
    Invert an attention mask (e.g., switches 0. and 1.).

    Args:
        encoder_attention_mask (`torch.Tensor`): An attention mask.

    Returns:
        `tf.Tensor`: The inverted attention mask.
    """
    if not isinstance(encoder_attention_mask, tf.Tensor):
        encoder_attention_mask = tf.convert_to_tensor(encoder_attention_mask)  # Catches stray NumPy inputs
    if encoder_attention_mask.shape.rank == 3:
        encoder_extended_attention_mask = encoder_attention_mask[:, None, :, :]
    if encoder_attention_mask.shape.rank == 2:
        encoder_extended_attention_mask = encoder_attention_mask[:, None, None, :]
    # T5 has a mask that can compare sequence ids, we can simulate this here with this transposition
    # Cf. https://github.com/tensorflow/mesh/blob/8d2465e9bc93129b913b5ccc6a59aa97abd96ec6/mesh_tensorflow
    # /transformer/transformer_layers.py#L270
    # encoder_extended_attention_mask = (encoder_extended_attention_mask ==
    # encoder_extended_attention_mask.transpose(-1, -2))
    encoder_extended_attention_mask = (
        tf.cast(1, encoder_attention_mask.dtype) - encoder_extended_attention_mask
    ) * encoder_extended_attention_mask.dtype.min

    return encoder_extended_attention_mask
