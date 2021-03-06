import torch
from torch import nn
import math
import torch.nn.functional as F


class Mish(torch.nn.Module):
    def __init__(self):
        super().__init__()

    def forward(self, x):
        x = x * (torch.tanh(torch.nn.functional.softplus(x)))
        return x


class Conv2dStaticSamePadding(nn.Module):
    """
    created by Zylo117
    The real keras/tensorflow conv2d with same padding
    """

    def __init__(
        self,
        in_channels,
        out_channels,
        kernel_size,
        stride=1,
        bias=True,
        groups=1,
        dilation=1,
        **kwargs
    ):
        super().__init__()
        self.conv = nn.Conv2d(
            in_channels,
            out_channels,
            kernel_size,
            stride=stride,
            bias=bias,
            groups=groups,
        )
        self.stride = self.conv.stride
        self.kernel_size = self.conv.kernel_size
        self.dilation = self.conv.dilation

        if isinstance(self.stride, int):
            self.stride = [self.stride] * 2
        elif len(self.stride) == 1:
            self.stride = [self.stride[0]] * 2

        if isinstance(self.kernel_size, int):
            self.kernel_size = [self.kernel_size] * 2
        elif len(self.kernel_size) == 1:
            self.kernel_size = [self.kernel_size[0]] * 2

    def forward(self, x):
        h, w = x.shape[-2:]

        extra_h = (
            (math.ceil(w / self.stride[1]) - 1) * self.stride[1]
            - w
            + self.kernel_size[1]
        )
        extra_v = (
            (math.ceil(h / self.stride[0]) - 1) * self.stride[0]
            - h
            + self.kernel_size[0]
        )

        left = extra_h // 2
        right = extra_h - left
        top = extra_v // 2
        bottom = extra_v - top

        x = F.pad(x, [left, right, top, bottom])

        x = self.conv(x)
        return x


class MaxPool2dStaticSamePadding(nn.Module):
    """
    created by Zylo117
    The real keras/tensorflow MaxPool2d with same padding
    """

    def __init__(self, *args, **kwargs):
        super().__init__()
        self.pool = nn.MaxPool2d(*args, **kwargs)
        self.stride = self.pool.stride
        self.kernel_size = self.pool.kernel_size

        if isinstance(self.stride, int):
            self.stride = [self.stride] * 2
        elif len(self.stride) == 1:
            self.stride = [self.stride[0]] * 2

        if isinstance(self.kernel_size, int):
            self.kernel_size = [self.kernel_size] * 2
        elif len(self.kernel_size) == 1:
            self.kernel_size = [self.kernel_size[0]] * 2

    def forward(self, x):
        h, w = x.shape[-2:]

        extra_h = (
            (math.ceil(w / self.stride[1]) - 1) * self.stride[1]
            - w
            + self.kernel_size[1]
        )
        extra_v = (
            (math.ceil(h / self.stride[0]) - 1) * self.stride[0]
            - h
            + self.kernel_size[0]
        )

        left = extra_h // 2
        right = extra_h - left
        top = extra_v // 2
        bottom = extra_v - top

        x = F.pad(x, [left, right, top, bottom])

        x = self.pool(x)
        return x


class SeparableConvBlock(nn.Module):
    """
    created by Zylo117
    """

    def __init__(self, in_channels, out_channels=None, norm=True, activation=False):
        super(SeparableConvBlock, self).__init__()
        if out_channels is None:
            out_channels = in_channels

        # Q: whether separate conv
        #  share bias between depthwise_conv and pointwise_conv
        #  or just pointwise_conv apply bias.
        # A: Confirmed, just pointwise_conv applies bias, depthwise_conv has no bias.

        self.depthwise_conv = Conv2dStaticSamePadding(
            in_channels,
            in_channels,
            kernel_size=3,
            stride=1,
            groups=in_channels,
            bias=False,
        )
        self.pointwise_conv = Conv2dStaticSamePadding(
            in_channels, out_channels, kernel_size=1, stride=1
        )

        self.norm = norm
        if self.norm:
            # Warning: pytorch momentum is different from tensorflow's, momentum_pytorch = 1 - momentum_tensorflow
            self.bn = nn.BatchNorm2d(num_features=out_channels, momentum=0.01, eps=1e-3)

        self.activation = activation
        if self.activation:
            self.mish = Mish()

    def forward(self, x):
        x = self.depthwise_conv(x)
        x = self.pointwise_conv(x)

        if self.norm:
            x = self.bn(x)

        if self.activation:
            x = self.mish(x)

        return x


class BiFPN(nn.Module):
    """
    modified by Zylo117
    """

    def __init__(
        self,
        num_channels,
        conv_channels,
        first_time=False,
        epsilon=1e-4,
        attention=True,
        use_p8=False,
    ):
        """
        Args:
            num_channels:
            conv_channels:
            first_time: whether the input comes directly from the efficientnet,
                        if True, downchannel it first, and downsample P5 to generate P6 then P7
            epsilon: epsilon of fast weighted attention sum of BiFPN, not the BN's epsilon
            onnx_export: if True, use Swish instead of MemoryEfficientSwish
        """
        super(BiFPN, self).__init__()
        self.epsilon = epsilon
        self.use_p8 = use_p8

        # Conv layers
        self.conv6_up = SeparableConvBlock(num_channels)
        self.conv5_up = SeparableConvBlock(num_channels)
        self.conv4_up = SeparableConvBlock(num_channels)
        self.conv3_up = SeparableConvBlock(num_channels)
        self.conv4_down = SeparableConvBlock(num_channels)
        self.conv5_down = SeparableConvBlock(num_channels)
        self.conv6_down = SeparableConvBlock(num_channels)
        self.conv7_down = SeparableConvBlock(num_channels)
        if use_p8:
            self.conv7_up = SeparableConvBlock(num_channels)
            self.conv8_down = SeparableConvBlock(num_channels)

        # Feature scaling layers
        self.p6_upsample = nn.Upsample(scale_factor=2, mode="nearest")
        self.p5_upsample = nn.Upsample(scale_factor=2, mode="nearest")
        self.p4_upsample = nn.Upsample(scale_factor=2, mode="nearest")
        self.p3_upsample = nn.Upsample(scale_factor=2, mode="nearest")

        self.p4_downsample = MaxPool2dStaticSamePadding(3, 2)
        self.p5_downsample = MaxPool2dStaticSamePadding(3, 2)
        self.p6_downsample = MaxPool2dStaticSamePadding(3, 2)
        self.p7_downsample = MaxPool2dStaticSamePadding(3, 2)
        if use_p8:
            self.p7_upsample = nn.Upsample(scale_factor=2, mode="nearest")
            self.p8_downsample = MaxPool2dStaticSamePadding(3, 2)

        self.swish = Mish()

        self.first_time = first_time
        if self.first_time:
            self.p5_down_channel = nn.Sequential(
                Conv2dStaticSamePadding(conv_channels[2], num_channels, 1),
                nn.BatchNorm2d(num_channels, momentum=0.01, eps=1e-3),
            )
            self.p4_down_channel = nn.Sequential(
                Conv2dStaticSamePadding(conv_channels[1], num_channels, 1),
                nn.BatchNorm2d(num_channels, momentum=0.01, eps=1e-3),
            )
            self.p3_down_channel = nn.Sequential(
                Conv2dStaticSamePadding(conv_channels[0], num_channels, 1),
                nn.BatchNorm2d(num_channels, momentum=0.01, eps=1e-3),
            )

            self.p5_to_p6 = nn.Sequential(
                Conv2dStaticSamePadding(conv_channels[2], num_channels, 1),
                nn.BatchNorm2d(num_channels, momentum=0.01, eps=1e-3),
                MaxPool2dStaticSamePadding(3, 2),
            )
            self.p6_to_p7 = nn.Sequential(MaxPool2dStaticSamePadding(3, 2))
            if use_p8:
                self.p7_to_p8 = nn.Sequential(MaxPool2dStaticSamePadding(3, 2))

            self.p4_down_channel_2 = nn.Sequential(
                Conv2dStaticSamePadding(conv_channels[1], num_channels, 1),
                nn.BatchNorm2d(num_channels, momentum=0.01, eps=1e-3),
            )
            self.p5_down_channel_2 = nn.Sequential(
                Conv2dStaticSamePadding(conv_channels[2], num_channels, 1),
                nn.BatchNorm2d(num_channels, momentum=0.01, eps=1e-3),
            )

        # Weight
        self.p6_w1 = nn.Parameter(
            torch.ones(2, dtype=torch.float32), requires_grad=True
        )
        self.p6_w1_relu = nn.ReLU()
        self.p5_w1 = nn.Parameter(
            torch.ones(2, dtype=torch.float32), requires_grad=True
        )
        self.p5_w1_relu = nn.ReLU()
        self.p4_w1 = nn.Parameter(
            torch.ones(2, dtype=torch.float32), requires_grad=True
        )
        self.p4_w1_relu = nn.ReLU()
        self.p3_w1 = nn.Parameter(
            torch.ones(2, dtype=torch.float32), requires_grad=True
        )
        self.p3_w1_relu = nn.ReLU()

        self.p4_w2 = nn.Parameter(
            torch.ones(3, dtype=torch.float32), requires_grad=True
        )
        self.p4_w2_relu = nn.ReLU()
        self.p5_w2 = nn.Parameter(
            torch.ones(3, dtype=torch.float32), requires_grad=True
        )
        self.p5_w2_relu = nn.ReLU()
        self.p6_w2 = nn.Parameter(
            torch.ones(3, dtype=torch.float32), requires_grad=True
        )
        self.p6_w2_relu = nn.ReLU()
        self.p7_w2 = nn.Parameter(
            torch.ones(2, dtype=torch.float32), requires_grad=True
        )
        self.p7_w2_relu = nn.ReLU()
        self.attention = attention

    def forward(self, inputs):
        """
        illustration of a minimal bifpn unit
            P7_0 -------------------------> P7_2 -------->
               |-------------|                ↑
                             ↓                |
            P6_0 ---------> P6_1 ---------> P6_2 -------->
               |-------------|--------------↑ ↑
                             ↓                |
            P5_0 ---------> P5_1 ---------> P5_2 -------->
               |-------------|--------------↑ ↑
                             ↓                |
            P4_0 ---------> P4_1 ---------> P4_2 -------->
               |-------------|--------------↑ ↑
                             |--------------↓ |
            P3_0 -------------------------> P3_2 -------->
        """

        # downsample channels using same-padding conv2d to target phase's if not the same
        # judge: same phase as target,
        # if same, pass;
        # elif earlier phase, downsample to target phase's by pooling
        # elif later phase, upsample to target phase's by nearest interpolation

        if self.attention:
            outs = self._forward_fast_attention(inputs)
        else:
            outs = self._forward(inputs)

        return outs

    def _forward_fast_attention(self, inputs):
        if self.first_time:
            p3, p4, p5 = inputs

            p6_in = self.p5_to_p6(p5)
            p7_in = self.p6_to_p7(p6_in)

            p3_in = self.p3_down_channel(p3)
            p4_in = self.p4_down_channel(p4)
            p5_in = self.p5_down_channel(p5)

        else:
            # P3_0, P4_0, P5_0, P6_0 and P7_0
            p3_in, p4_in, p5_in, p6_in, p7_in = inputs

        # P7_0 to P7_2

        # Weights for P6_0 and P7_0 to P6_1
        p6_w1 = self.p6_w1_relu(self.p6_w1)
        weight = p6_w1 / (torch.sum(p6_w1, dim=0) + self.epsilon)
        # Connections for P6_0 and P7_0 to P6_1 respectively
        p6_up = self.conv6_up(
            self.swish(weight[0] * p6_in + weight[1] * self.p6_upsample(p7_in))
        )

        # Weights for P5_0 and P6_1 to P5_1
        p5_w1 = self.p5_w1_relu(self.p5_w1)
        weight = p5_w1 / (torch.sum(p5_w1, dim=0) + self.epsilon)
        # Connections for P5_0 and P6_1 to P5_1 respectively
        p5_up = self.conv5_up(
            self.swish(weight[0] * p5_in + weight[1] * self.p5_upsample(p6_up))
        )

        # Weights for P4_0 and P5_1 to P4_1
        p4_w1 = self.p4_w1_relu(self.p4_w1)
        weight = p4_w1 / (torch.sum(p4_w1, dim=0) + self.epsilon)
        # Connections for P4_0 and P5_1 to P4_1 respectively
        p4_up = self.conv4_up(
            self.swish(weight[0] * p4_in + weight[1] * self.p4_upsample(p5_up))
        )

        # Weights for P3_0 and P4_1 to P3_2
        p3_w1 = self.p3_w1_relu(self.p3_w1)
        weight = p3_w1 / (torch.sum(p3_w1, dim=0) + self.epsilon)
        # Connections for P3_0 and P4_1 to P3_2 respectively
        p3_out = self.conv3_up(
            self.swish(weight[0] * p3_in + weight[1] * self.p3_upsample(p4_up))
        )

        if self.first_time:
            p4_in = self.p4_down_channel_2(p4)
            p5_in = self.p5_down_channel_2(p5)

        # Weights for P4_0, P4_1 and P3_2 to P4_2
        p4_w2 = self.p4_w2_relu(self.p4_w2)
        weight = p4_w2 / (torch.sum(p4_w2, dim=0) + self.epsilon)
        # Connections for P4_0, P4_1 and P3_2 to P4_2 respectively
        p4_out = self.conv4_down(
            self.swish(
                weight[0] * p4_in
                + weight[1] * p4_up
                + weight[2] * self.p4_downsample(p3_out)
            )
        )

        # Weights for P5_0, P5_1 and P4_2 to P5_2
        p5_w2 = self.p5_w2_relu(self.p5_w2)
        weight = p5_w2 / (torch.sum(p5_w2, dim=0) + self.epsilon)
        # Connections for P5_0, P5_1 and P4_2 to P5_2 respectively
        p5_out = self.conv5_down(
            self.swish(
                weight[0] * p5_in
                + weight[1] * p5_up
                + weight[2] * self.p5_downsample(p4_out)
            )
        )

        # Weights for P6_0, P6_1 and P5_2 to P6_2
        p6_w2 = self.p6_w2_relu(self.p6_w2)
        weight = p6_w2 / (torch.sum(p6_w2, dim=0) + self.epsilon)
        # Connections for P6_0, P6_1 and P5_2 to P6_2 respectively
        p6_out = self.conv6_down(
            self.swish(
                weight[0] * p6_in
                + weight[1] * p6_up
                + weight[2] * self.p6_downsample(p5_out)
            )
        )

        # Weights for P7_0 and P6_2 to P7_2
        p7_w2 = self.p7_w2_relu(self.p7_w2)
        weight = p7_w2 / (torch.sum(p7_w2, dim=0) + self.epsilon)
        # Connections for P7_0 and P6_2 to P7_2
        p7_out = self.conv7_down(
            self.swish(weight[0] * p7_in + weight[1] * self.p7_downsample(p6_out))
        )

        return p3_out, p4_out, p5_out, p6_out, p7_out

    def _forward(self, inputs):
        if self.first_time:
            p3, p4, p5 = inputs

            p6_in = self.p5_to_p6(p5)
            p7_in = self.p6_to_p7(p6_in)
            if self.use_p8:
                p8_in = self.p7_to_p8(p7_in)

            p3_in = self.p3_down_channel(p3)
            p4_in = self.p4_down_channel(p4)
            p5_in = self.p5_down_channel(p5)

        else:
            if self.use_p8:
                # P3_0, P4_0, P5_0, P6_0, P7_0 and P8_0
                p3_in, p4_in, p5_in, p6_in, p7_in, p8_in = inputs
            else:
                # P3_0, P4_0, P5_0, P6_0 and P7_0
                p3_in, p4_in, p5_in, p6_in, p7_in = inputs

        if self.use_p8:
            # P8_0 to P8_2

            # Connections for P7_0 and P8_0 to P7_1 respectively
            p7_up = self.conv7_up(self.swish(p7_in + self.p7_upsample(p8_in)))

            # Connections for P6_0 and P7_0 to P6_1 respectively
            p6_up = self.conv6_up(self.swish(p6_in + self.p6_upsample(p7_up)))
        else:
            # P7_0 to P7_2

            # Connections for P6_0 and P7_0 to P6_1 respectively
            p6_up = self.conv6_up(self.swish(p6_in + self.p6_upsample(p7_in)))

        # Connections for P5_0 and P6_1 to P5_1 respectively
        p5_up = self.conv5_up(self.swish(p5_in + self.p5_upsample(p6_up)))

        # Connections for P4_0 and P5_1 to P4_1 respectively
        p4_up = self.conv4_up(self.swish(p4_in + self.p4_upsample(p5_up)))

        # Connections for P3_0 and P4_1 to P3_2 respectively
        p3_out = self.conv3_up(self.swish(p3_in + self.p3_upsample(p4_up)))

        if self.first_time:
            p4_in = self.p4_down_channel_2(p4)
            p5_in = self.p5_down_channel_2(p5)

        # Connections for P4_0, P4_1 and P3_2 to P4_2 respectively
        p4_out = self.conv4_down(self.swish(p4_in + p4_up + self.p4_downsample(p3_out)))

        # Connections for P5_0, P5_1 and P4_2 to P5_2 respectively
        p5_out = self.conv5_down(self.swish(p5_in + p5_up + self.p5_downsample(p4_out)))

        # Connections for P6_0, P6_1 and P5_2 to P6_2 respectively
        p6_out = self.conv6_down(self.swish(p6_in + p6_up + self.p6_downsample(p5_out)))

        if self.use_p8:
            # Connections for P7_0, P7_1 and P6_2 to P7_2 respectively
            p7_out = self.conv7_down(
                self.swish(p7_in + p7_up + self.p7_downsample(p6_out))
            )

            # Connections for P8_0 and P7_2 to P8_2
            p8_out = self.conv8_down(self.swish(p8_in + self.p8_downsample(p7_out)))

            return p3_out, p4_out, p5_out, p6_out, p7_out, p8_out
        else:
            # Connections for P7_0 and P6_2 to P7_2
            p7_out = self.conv7_down(self.swish(p7_in + self.p7_downsample(p6_out)))

            return p3_out, p4_out, p5_out, p6_out, p7_out


class FPN(nn.Module):
    """only for resnet50,101,152"""

    def __init__(self, features=256, use_p5=True):
        super(FPN, self).__init__()
        self.prj_5 = nn.Conv2d(1280, features, kernel_size=1)
        self.prj_4 = nn.Conv2d(640, features, kernel_size=1)
        self.prj_3 = nn.Conv2d(320, features, kernel_size=1)
        self.conv_5 = nn.Conv2d(features, features, kernel_size=3, padding=1)
        self.conv_4 = nn.Conv2d(features, features, kernel_size=3, padding=1)
        self.conv_3 = nn.Conv2d(features, features, kernel_size=3, padding=1)
        if use_p5:
            self.conv_out6 = nn.Conv2d(
                features, features, kernel_size=3, padding=1, stride=2
            )
        else:
            self.conv_out6 = nn.Conv2d(
                2048, features, kernel_size=3, padding=1, stride=2
            )
        self.conv_out7 = nn.Conv2d(
            features, features, kernel_size=3, padding=1, stride=2
        )
        self.use_p5 = use_p5
        self.apply(self.init_conv_kaiming)

    def upsamplelike(self, inputs):
        src, target = inputs
        return F.interpolate(
            src, size=(target.shape[2], target.shape[3]), mode="nearest"
        )

    def init_conv_kaiming(self, module):
        if isinstance(module, nn.Conv2d):
            nn.init.kaiming_uniform_(module.weight, a=1)

            if module.bias is not None:
                nn.init.constant_(module.bias, 0)

    def forward(self, x):
        C3, C4, C5 = x
        P5 = self.prj_5(C5)
        P4 = self.prj_4(C4)
        P3 = self.prj_3(C3)

        P4 = P4 + self.upsamplelike([P5, C4])
        P3 = P3 + self.upsamplelike([P4, C3])

        P3 = self.conv_3(P3)
        P4 = self.conv_4(P4)
        P5 = self.conv_5(P5)

        P5 = P5 if self.use_p5 else C5
        P6 = self.conv_out6(P5)
        P7 = self.conv_out7(F.relu(P6))
        return [P3, P4, P5, P6, P7]


if __name__ == "__main__":
    fpn = nn.Sequential(
        *[
            BiFPN(256, [320, 640, 1280], True if _ == 0 else False, attention=True)
            for _ in range(2)
        ]
    )
    fpn(
        (
            torch.zeros((2, 320, 32, 176)),
            torch.zeros((2, 640, 16, 88)),
            torch.zeros((2, 1280, 8, 44)),
        )
    )
