# Copyright 2018 Xiaomi, Inc.  All rights reserved.
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


import math
import numpy as np
import tensorflow as tf
from enum import Enum

from mace.proto import mace_pb2
from mace.python.tools.converter_tool import base_converter
from mace.python.tools.converter_tool.base_converter import PoolingType
from mace.python.tools.converter_tool.base_converter import PaddingMode
from mace.python.tools.converter_tool.base_converter import ActivationType
from mace.python.tools.converter_tool.base_converter import EltwiseType
from mace.python.tools.converter_tool.base_converter import DataFormat
from mace.python.tools.converter_tool.base_converter import FilterFormat
from mace.python.tools.converter_tool.base_converter import MaceOp
from mace.python.tools.converter_tool.base_converter import MaceKeyword
from mace.python.tools.converter_tool.base_converter import ConverterUtil
from mace.python.tools.convert_util import mace_check

from tensorflow.core.framework import tensor_shape_pb2

tf_padding_str = 'padding'
tf_strides_str = 'strides'
tf_dilations_str = 'dilations'
tf_data_format_str = 'data_format'
tf_kernel_str = 'ksize'
tf_epsilon_str = 'epsilon'
tf_align_corners = 'align_corners'
tf_block_size = 'block_size'

TFSupportedOps = [
    'Conv2D',
    'DepthwiseConv2dNative',
    'Conv2DBackpropInput',
    'BiasAdd',
    'Add',
    'Sub',
    'Mul',
    'Div',
    'Min',
    'Max',
    'Neg',
    'Abs',
    'RealDiv',
    'SquaredDifference',
    'Pow',
    'Relu',
    'Relu6',
    'Tanh',
    'Sigmoid',
    'FusedBatchNorm',
    'AvgPool',
    'MaxPool',
    'Squeeze',
    'MatMul',
    'Identity',
    'Reshape',
    'Shape',
    'Transpose',
    'Softmax',
    'ResizeBilinear',
    'Placeholder',
    'SpaceToBatchND',
    'BatchToSpaceND',
    'DepthToSpace',
    'SpaceToDepth',
    'Pad',
    'ConcatV2',
    'Mean',
    'Const',
]

TFOpType = Enum('TFOpType', [(op, op) for op in TFSupportedOps], type=str)


class TensorflowConverter(base_converter.ConverterInterface):
    """A class for convert tensorflow frozen model to mace model.
    We use tensorflow engine to infer op output shapes, since they are of
    too many types."""

    padding_mode = {
        'VALID': PaddingMode.VALID,
        'SAME': PaddingMode.SAME,
        'FULL': PaddingMode.FULL
    }
    pooling_type_mode = {
        TFOpType.AvgPool.name: PoolingType.AVG,
        TFOpType.MaxPool.name: PoolingType.MAX
    }
    eltwise_type = {
        TFOpType.Add.name: EltwiseType.SUM,
        TFOpType.Sub.name: EltwiseType.SUB,
        TFOpType.Mul.name: EltwiseType.PROD,
        TFOpType.Div.name: EltwiseType.DIV,
        TFOpType.Min.name: EltwiseType.MIN,
        TFOpType.Max.name: EltwiseType.MAX,
        TFOpType.Neg.name: EltwiseType.NEG,
        TFOpType.Abs.name: EltwiseType.ABS,
        TFOpType.RealDiv.name: EltwiseType.DIV,
        TFOpType.SquaredDifference.name: EltwiseType.SQR_DIFF,
        TFOpType.Pow.name: EltwiseType.POW
    }
    activation_type = {
        TFOpType.Relu.name: ActivationType.RELU,
        TFOpType.Relu6.name: ActivationType.RELUX,
        TFOpType.Tanh.name: ActivationType.TANH,
        TFOpType.Sigmoid.name: ActivationType.SIGMOID
    }

    def __init__(self, option, src_model_file):
        self._op_converters = {
            TFOpType.Conv2D.name: self.convert_conv2d,
            TFOpType.DepthwiseConv2dNative.name: self.convert_conv2d,
            TFOpType.Conv2DBackpropInput.name: self.convert_conv2d,
            TFOpType.BiasAdd.name: self.convert_biasadd,
            TFOpType.Add.name: self.convert_add,
            TFOpType.Sub.name: self.convert_elementwise,
            TFOpType.Mul.name: self.convert_elementwise,
            TFOpType.Div.name: self.convert_elementwise,
            TFOpType.Min.name: self.convert_elementwise,
            TFOpType.Max.name: self.convert_elementwise,
            TFOpType.Neg.name: self.convert_elementwise,
            TFOpType.Abs.name: self.convert_elementwise,
            TFOpType.RealDiv.name: self.convert_elementwise,
            TFOpType.SquaredDifference.name: self.convert_elementwise,
            TFOpType.Pow.name: self.convert_elementwise,
            TFOpType.Relu.name: self.convert_activation,
            TFOpType.Relu6.name: self.convert_activation,
            TFOpType.Tanh.name: self.convert_activation,
            TFOpType.Sigmoid.name: self.convert_activation,
            TFOpType.FusedBatchNorm.name: self.convert_fused_batchnorm,
            TFOpType.AvgPool.name: self.convert_pooling,
            TFOpType.MaxPool.name: self.convert_pooling,
            TFOpType.Squeeze.name: self.convert_identity,
            TFOpType.MatMul.name: self.convert_matmul,
            TFOpType.Identity.name: self.convert_identity,
            TFOpType.Reshape.name: self.convert_reshape,
            TFOpType.Shape.name: self.convert_nop,
            TFOpType.Transpose.name: self.convert_transpose,
            TFOpType.Softmax.name: self.convert_softmax,
            TFOpType.ResizeBilinear.name: self.convert_resize_bilinear,
            TFOpType.Placeholder.name: self.convert_nop,
            TFOpType.SpaceToBatchND.name: self.convert_space_batch,
            TFOpType.BatchToSpaceND.name: self.convert_space_batch,
            TFOpType.DepthToSpace.name: self.convert_space_depth,
            TFOpType.SpaceToDepth.name: self.convert_space_depth,
            TFOpType.Pad.name: self.convert_pad,
            TFOpType.ConcatV2.name: self.convert_concat,
            TFOpType.Mean.name: self.convert_mean,
            TFOpType.Const.name: self.convert_nop,
        }
        self._option = option
        self._mace_net_def = mace_pb2.NetDef()
        ConverterUtil.set_filter_format(self._mace_net_def, FilterFormat.HWIO)
        tf_graph_def = tf.GraphDef()
        with tf.gfile.Open(src_model_file, 'rb') as f:
            tf_graph_def.ParseFromString(f.read())
        self.add_shape_info(tf_graph_def)

        with tf.Session() as session:
            with session.graph.as_default() as graph:
                tf.import_graph_def(tf_graph_def, name='')
                self._tf_graph = graph

        self._skip_tensor = set()

    def run(self):
        with tf.Session() as session:
            self.convert_ops()

        self.replace_input_output_tensor_name()
        return self._mace_net_def

    def replace_input_output_tensor_name(self):
        for op in self._mace_net_def.op:
            for i in xrange(len(op.input)):
                if op.input[i][-2:] == ':0':
                    op_name = op.input[i][:-2]
                    if op_name in self._option.input_nodes \
                            or op_name in self._option.output_nodes:
                        op.input[i] = op_name
            for i in xrange(len(op.output)):
                if op.output[i][-2:] == ':0':
                    op_name = op.output[i][:-2]
                    if op_name in self._option.output_nodes:
                        op.output[i] = op_name

    def add_shape_info(self, tf_graph_def):
        for node in tf_graph_def.node:
            for input_node in self._option.input_nodes.values():
                if node.name == input_node.name \
                        or node.name + ':0' == input_node.name:
                    del node.attr['shape'].shape.dim[:]
                    node.attr['shape'].shape.dim.extend([
                        tensor_shape_pb2.TensorShapeProto.Dim(size=i) for i in
                        input_node.shape
                    ])

    @staticmethod
    def get_scope(tensor_name):
        idx = tensor_name.rfind('/')
        if idx == -1:
            return tensor_name
        else:
            return tensor_name[:idx]

    def convert_ops(self):
        for tf_op in self._tf_graph.get_operations():
            mace_check(tf_op.type in self._op_converters,
                       "Mace does not support tensorflow op type %s yet"
                       % tf_op.type)
            self._op_converters[tf_op.type](tf_op)
        self.convert_tensors()

    def convert_tensors(self):
        for tf_op in self._tf_graph.get_operations():
            if tf_op.type != TFOpType.Const.name:
                continue
            output_name = tf_op.outputs[0].name
            if output_name not in self._skip_tensor:
                tensor = self._mace_net_def.tensors.add()
                tensor.name = tf_op.outputs[0].name
                tf_tensor = tf_op.outputs[0].eval()
                tensor.dims.extend(list(tf_tensor.shape))

                tf_dt = tf_op.get_attr('dtype')
                if tf_dt == tf.float32:
                    tensor.data_type = mace_pb2.DT_FLOAT
                    tensor.float_data.extend(tf_tensor.astype(np.float32).flat)
                elif tf_dt == tf.int32:
                    tensor.data_type = mace_pb2.DT_INT32
                    tensor.int32_data.extend(tf_tensor.astype(np.int32).flat)
                else:
                    mace_check(False,
                               "Not supported tensor type: %s" % tf_dt.name)

    def add_tensor(self, name, shape, data_type, value):
        tensor = self._mace_net_def.tensors.add()
        tensor.name = name
        tensor.dims.extend(list(shape))
        tensor.data_type = data_type
        tensor.float_data.extend(value.flat)

    def convert_nop(self, tf_op):
        pass

    def convert_general_op(self, tf_op):
        op = self._mace_net_def.op.add()
        op.name = tf_op.name
        op.type = tf_op.type
        op.input.extend([tf_input.name for tf_input in tf_op.inputs])
        op.output.extend([tf_output.name for tf_output in tf_op.outputs])
        for tf_output in tf_op.outputs:
            output_shape = op.output_shape.add()
            output_shape.dims.extend(tf_output.shape.as_list())

        ConverterUtil.add_data_format_arg(op, DataFormat.NHWC)

        return op

    def convert_identity(self, tf_op):
        op = self.convert_general_op(tf_op)
        op.type = 'Identity'

    def convert_conv2d(self, tf_op):
        op = self.convert_general_op(tf_op)
        if tf_op.type == TFOpType.DepthwiseConv2dNative.name:
            op.type = MaceOp.DepthwiseConv2d.name
        elif tf_op.type == TFOpType.Conv2DBackpropInput.name:
            op.type = MaceOp.Deconv2D.name
        else:
            op.type = MaceOp.Conv2D.name

        padding_arg = op.arg.add()
        padding_arg.name = MaceKeyword.mace_padding_str
        padding_arg.i = self.padding_mode[tf_op.get_attr(tf_padding_str)].value
        strides_arg = op.arg.add()
        strides_arg.name = MaceKeyword.mace_strides_str
        strides_arg.ints.extend(tf_op.get_attr(tf_strides_str)[1:3])
        if op.type != MaceOp.Deconv2D.name:
            dilation_arg = op.arg.add()
            dilation_arg.name = MaceKeyword.mace_dilations_str
            try:
                dilation_val = tf_op.get_attr(tf_dilations_str)[1:3]
            except ValueError:
                dilation_val = [1, 1]

            dilation_arg.ints.extend(dilation_val)

    def convert_elementwise(self, tf_op):
        op = self.convert_general_op(tf_op)
        op.type = MaceOp.Eltwise.name

        type_arg = op.arg.add()
        type_arg.name = MaceKeyword.mace_element_type_str
        type_arg.i = self.eltwise_type[tf_op.type].value

    def convert_biasadd(self, tf_op):
        op = self.convert_general_op(tf_op)
        op.type = MaceOp.BiasAdd.name

    def convert_add(self, tf_op):
        if len(tf_op.inputs) == 2:
            self.convert_elementwise(tf_op)
        else:
            op = self.convert_general_op(tf_op)
            op.type = MaceOp.AddN.name

    def convert_activation(self, tf_op):
        op = self.convert_general_op(tf_op)
        op.type = MaceOp.Activation.name

        type_arg = op.arg.add()
        type_arg.name = MaceKeyword.mace_activation_type_str
        type_arg.s = self.activation_type[tf_op.type].name

        if tf_op.type == TFOpType.Relu6.name:
            limit_arg = op.arg.add()
            limit_arg.name = MaceKeyword.mace_activation_max_limit_str
            limit_arg.f = 6.0

    def convert_fused_batchnorm(self, tf_op):
        op = self.convert_general_op(tf_op)
        op.type = MaceOp.FoldedBatchNorm.name

        gamma_value = tf_op.inputs[1].eval().astype(np.float32)
        beta_value = tf_op.inputs[2].eval().astype(np.float32)
        mean_value = tf_op.inputs[3].eval().astype(np.float32)
        var_value = tf_op.inputs[4].eval().astype(np.float32)
        epsilon_value = tf_op.get_attr(tf_epsilon_str)

        scale_name = self.get_scope(tf_op.name) + '/scale:0'
        offset_name = self.get_scope(tf_op.name) + '/offset:0'
        scale_value = (
            (1.0 / np.vectorize(math.sqrt)(
                var_value + epsilon_value)) * gamma_value)
        offset_value = (-mean_value * scale_value) + beta_value
        self.add_tensor(scale_name, scale_value.shape, mace_pb2.DT_FLOAT,
                        scale_value)
        self.add_tensor(offset_name, offset_value.shape, mace_pb2.DT_FLOAT,
                        offset_value)
        self._skip_tensor.update([inp.name for inp in tf_op.inputs][1:])

        del op.input[1:]
        op.input.extend([scale_name, offset_name])
        del op.output[1:]
        del op.output_shape[1:]

    def convert_pooling(self, tf_op):
        op = self.convert_general_op(tf_op)
        op.type = MaceOp.Pooling.name
        pooling_type_arg = op.arg.add()
        pooling_type_arg.name = MaceKeyword.mace_pooling_type_str
        pooling_type_arg.i = self.pooling_type_mode[tf_op.type].value
        padding_arg = op.arg.add()
        padding_arg.name = MaceKeyword.mace_padding_str
        padding_arg.i = self.padding_mode[tf_op.get_attr(tf_padding_str)].value
        strides_arg = op.arg.add()
        strides_arg.name = MaceKeyword.mace_strides_str
        strides_arg.ints.extend(tf_op.get_attr(tf_strides_str)[1:3])
        kernels_arg = op.arg.add()
        kernels_arg.name = MaceKeyword.mace_kernel_str
        kernels_arg.ints.extend(tf_op.get_attr(tf_kernel_str)[1:3])

    def convert_softmax(self, tf_op):
        op = self.convert_general_op(tf_op)
        op.type = MaceOp.Softmax.name

    def convert_resize_bilinear(self, tf_op):
        op = self.convert_general_op(tf_op)
        op.type = MaceOp.ResizeBilinear.name
        del op.input[1:]

        size_arg = op.arg.add()
        size_arg.name = MaceKeyword.mace_resize_size_str
        size_value = tf_op.inputs[1].eval().astype(np.int32)
        size_arg.ints.extend(size_value)
        self._skip_tensor.add(tf_op.inputs[1].name)
        align_corners_arg = op.arg.add()
        align_corners_arg.name = MaceKeyword.mace_align_corners_str
        align_corners_arg.i = tf_op.get_attr(tf_align_corners)

    def convert_space_batch(self, tf_op):

        op = self.convert_general_op(tf_op)
        del op.input[1:]

        size_arg = op.arg.add()
        size_arg.name = MaceKeyword.mace_space_batch_block_shape_str
        size_value = tf_op.inputs[1].eval().astype(np.int32)
        size_arg.ints.extend(size_value)

        crops_or_paddings_arg = op.arg.add()
        if op.type == TFOpType.BatchToSpaceND.name:
            op.type = MaceOp.BatchToSpaceND.name
            crops_or_paddings_arg.name = \
                MaceKeyword.mace_batch_to_space_crops_str
        else:
            op.type = MaceOp.SpaceToBatchND.name
            crops_or_paddings_arg.name = MaceKeyword.mace_paddings_str
        crops_or_paddings_value = tf_op.inputs[2].eval().astype(np.int32).flat
        crops_or_paddings_arg.ints.extend(crops_or_paddings_value)

        self._skip_tensor.add(tf_op.inputs[1].name)
        self._skip_tensor.add(tf_op.inputs[2].name)

    def convert_space_depth(self, tf_op):
        op = self.convert_general_op(tf_op)
        if op.type == TFOpType.SpaceToDepth.name:
            op.type = MaceOp.SpaceToDepth.name
        else:
            op.type = MaceOp.DepthToSpace.name

        size_arg = op.arg.add()
        size_arg.name = MaceKeyword.mace_space_depth_block_size_str
        size_arg.i = tf_op.get_attr(tf_block_size)

    def convert_pad(self, tf_op):
        op = self.convert_general_op(tf_op)
        op.type = MaceOp.Pad.name
        del op.input[1:]

        paddings_arg = op.arg.add()
        paddings_arg.name = MaceKeyword.mace_paddings_str
        paddings_value = tf_op.inputs[1].eval().astype(np.int32).flat
        paddings_arg.ints.extend(paddings_value)
        self._skip_tensor.add(tf_op.inputs[1].name)

        if len(tf_op.inputs) == 3:
            constant_value_arg = op.arg.add()
            constant_value_arg.name = MaceKeyword.mace_constant_value_str
            constant_value = tf_op.inputs[2].eval().astype(np.int32).flat[0]
            constant_value_arg.i = constant_value
            self._skip_tensor.add(tf_op.inputs[2].name)

    def convert_concat(self, tf_op):
        op = self.convert_general_op(tf_op)
        op.type = MaceOp.Concat.name
        del op.input[-1]

        axis_arg = op.arg.add()
        axis_arg.name = MaceKeyword.mace_axis_str
        axis = tf_op.inputs[-1].eval().astype(np.int32)
        axis = 4 + axis if axis < 0 else axis
        axis_arg.i = axis

        mace_check(axis == 3, "only support concat at channel dimension")

        self._skip_tensor.add(tf_op.inputs[-1].name)

    def convert_matmul(self, tf_op):
        op = self.convert_general_op(tf_op)
        op.type = MaceOp.MatMul.name

    def convert_reshape(self, tf_op):
        op = self.convert_general_op(tf_op)
        op.type = MaceOp.Reshape.name
        del op.input[1:]

        shape_arg = op.arg.add()
        shape_arg.name = MaceKeyword.mace_shape_str
        shape_value = []
        if tf_op.inputs[1].op.type == TFOpType.Const.name:
            shape_value = list(tf_op.inputs[1].eval().astype(np.int32))
            for i in xrange(len(shape_value)):
                if shape_value[i] == -1:
                    shape_value[i] = 1
            self._skip_tensor.add(tf_op.inputs[-1].name)
        elif tf_op.inputs[1].op.type == TFOpType.Shape.name:
            shape_value = list(tf_op.inputs[1].op.inputs[0].shape.as_list())

        shape_arg.ints.extend(shape_value)

    def convert_transpose(self, tf_op):
        perm = tf_op.inputs[1].eval().astype(np.int32)
        ordered_perm = np.sort(perm)

        mace_check(np.array_equal(perm, ordered_perm),
                   "Transpose not supported yet, only internal transpose"
                   " in composed ops might be supported")

        op = self.convert_general_op(tf_op)
        op.type = 'Identity'
        del op.input[1:]

        self._skip_tensor.add(tf_op.inputs[1].name)

    def convert_mean(self, tf_op):
        op = self.convert_general_op(tf_op)
        del op.input[1:]

        reduce_dims = tf_op.inputs[1].eval()
        mace_check(reduce_dims[0] == 1 and reduce_dims[1] == 2,
                   "Mean only support reduce dim 1, 2")

        op.type = MaceOp.Pooling.name
        pooling_type_arg = op.arg.add()
        pooling_type_arg.name = MaceKeyword.mace_pooling_type_str
        pooling_type_arg.i = PoolingType.AVG.value
        padding_arg = op.arg.add()
        padding_arg.name = MaceKeyword.mace_padding_str
        padding_arg.i = PaddingMode.VALID.value
        strides_arg = op.arg.add()
        strides_arg.name = MaceKeyword.mace_strides_str
        strides_arg.ints.extend([1, 1])
        kernels_arg = op.arg.add()
        kernels_arg.name = MaceKeyword.mace_kernel_str
        kernels_arg.ints.extend(tf_op.inputs[0].shape.as_list()[1:3])

        self._skip_tensor.add(tf_op.inputs[1].name)
