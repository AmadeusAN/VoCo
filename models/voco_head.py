# Copyright 2020 - 2022 MONAI Consortium
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import torch
import torch.nn as nn
import numpy as np
from monai.networks.nets.swin_unetr import *
from monai.networks.blocks import (
    PatchEmbed,
    UnetOutBlock,
    UnetrBasicBlock,
    UnetrUpBlock,
)
from monai.networks.nets.swin_unetr import SwinTransformer as SwinViT
from monai.utils import ensure_tuple_rep
import argparse
import torch.nn.functional as F
from utils import config


class projection_head(nn.Module):
    def __init__(self, in_dim=864, hidden_dim=2048, out_dim=2048):
        super().__init__()
        self.layer1 = nn.Sequential(
            nn.Linear(in_dim, hidden_dim),
            nn.BatchNorm1d(hidden_dim, affine=False, track_running_stats=False),
            nn.ReLU(inplace=True),
        )
        self.layer2 = nn.Sequential(
            nn.Linear(hidden_dim, hidden_dim),
            nn.BatchNorm1d(hidden_dim, affine=False, track_running_stats=False),
            nn.ReLU(inplace=True),
        )
        self.layer3 = nn.Sequential(
            nn.Linear(hidden_dim, out_dim),
        )
        self.out_dim = out_dim

    def forward(self, input):
        if torch.is_tensor(input):
            x = input
        else:
            x = input[-1]
            b = x.size()[0]
            x = F.adaptive_avg_pool3d(x, (1, 1, 1)).view(b, -1)

        x = self.layer1(x)
        x = self.layer2(x)
        x = self.layer3(x)

        return x


class Swin(nn.Module):
    def __init__(self, args):
        super(Swin, self).__init__()
        patch_size = ensure_tuple_rep(2, args.spatial_dims)
        window_size = ensure_tuple_rep(7, args.spatial_dims)
        self.swinViT = SwinViT(
            in_chans=args.in_channels,
            embed_dim=args.feature_size,
            window_size=window_size,
            patch_size=patch_size,
            depths=[2, 2, 2, 2],
            num_heads=config.vit_heads,
            mlp_ratio=4.0,
            qkv_bias=True,
            drop_rate=0.0,
            attn_drop_rate=0.0,
            drop_path_rate=args.dropout_path_rate,
            norm_layer=torch.nn.LayerNorm,
            use_checkpoint=args.use_checkpoint,
            spatial_dims=args.spatial_dims,
            use_v2=True,
        )
        norm_name = "instance"
        self.encoder1 = UnetrBasicBlock(
            spatial_dims=args.spatial_dims,
            in_channels=args.in_channels,
            out_channels=args.feature_size,
            kernel_size=3,
            stride=1,
            norm_name=norm_name,
            res_block=True,
        )

        self.encoder2 = UnetrBasicBlock(
            spatial_dims=args.spatial_dims,
            in_channels=args.feature_size,
            out_channels=args.feature_size,
            kernel_size=3,
            stride=1,
            norm_name=norm_name,
            res_block=True,
        )

        self.encoder3 = UnetrBasicBlock(
            spatial_dims=args.spatial_dims,
            in_channels=2 * args.feature_size,
            out_channels=2 * args.feature_size,
            kernel_size=3,
            stride=1,
            norm_name=norm_name,
            res_block=True,
        )

        self.encoder4 = UnetrBasicBlock(
            spatial_dims=args.spatial_dims,
            in_channels=4 * args.feature_size,
            out_channels=4 * args.feature_size,
            kernel_size=3,
            stride=1,
            norm_name=norm_name,
            res_block=True,
        )

        self.encoder10 = UnetrBasicBlock(
            spatial_dims=args.spatial_dims,
            in_channels=16 * args.feature_size,
            out_channels=16 * args.feature_size,
            kernel_size=3,
            stride=1,
            norm_name=norm_name,
            res_block=True,
        )

        self.proj_head = projection_head(in_dim=864, hidden_dim=1024, out_dim=1024)

    def forward_encs(self, encs):
        b = encs[0].size()[0]
        outs = []
        for enc in encs:
            out = F.adaptive_avg_pool3d(enc, (1, 1, 1))
            outs.append(out.view(b, -1))
        outs = torch.cat(outs, dim=1)
        return outs

    def forward(self, x_in):
        b = x_in.size()[0]
        hidden_states_out = self.swinViT(x_in)

        enc0 = self.encoder1(x_in)
        enc1 = self.encoder2(hidden_states_out[0])
        enc2 = self.encoder3(hidden_states_out[1])
        enc3 = self.encoder4(hidden_states_out[2])
        dec4 = self.encoder10(hidden_states_out[4])

        encs = [enc0, enc1, enc2, enc3, dec4]

        # for enc in encs:
        #     print(enc.shape)

        out = self.forward_encs(encs)
        out = self.proj_head(out.view(b, -1))
        return out


class VoCoHead(nn.Module):
    def __init__(self, args):
        super(VoCoHead, self).__init__()
        self.student = Swin(args)
        self.teacher = Swin(args)

    @torch.no_grad()
    def _EMA_update_encoder_teacher(self):
        ## no scheduler here
        momentum = 0.9
        for param, param_t in zip(self.student.parameters(), self.teacher.parameters()):
            param_t.data = momentum * param_t.data + (1.0 - momentum) * param.data

    def forward(self, img, crops, labels):
        batch_size = labels.size()[0]
        total_size = img.size()[0]
        sw_size = total_size // batch_size
        pos, neg, total_b_loss = 0.0, 0.0, 0.0

        img, crops = img.as_tensor(), crops.as_tensor()
        inputs = torch.cat([img, crops], dim=0)

        # here we do norm on all instances
        students_all = self.student(inputs)
        self._EMA_update_encoder_teacher()
        with torch.no_grad():
            teachers_all = (self.teacher(inputs)).detach()

        x_stu_all, bases_stu_all = students_all[:total_size], students_all[total_size:]
        x_tea_all, bases_tea_all = teachers_all[:total_size], teachers_all[total_size:]

        for i in range(batch_size):
            label = labels[i]

            x_stu, bases_stu = (
                x_stu_all[i * sw_size : (i + 1) * sw_size],
                bases_stu_all[i * 16 : (i + 1) * 16],
            )
            x_tea, bases_tea = (
                x_tea_all[i * sw_size : (i + 1) * sw_size],
                bases_tea_all[i * 16 : (i + 1) * 16],
            )

            # Accoording to paper. logits come from cosine similarity, followed by ReLU
            logits1 = online_assign(x_stu, bases_tea)
            logits2 = online_assign(x_tea, bases_stu)

            logits = (logits1 + logits2) * 0.5

            if i == 0:
                print("labels and logits:", label[0].data, logits[0].data)

            pos_loss, neg_loss = ce_loss(label, logits)
            pos += pos_loss
            neg += neg_loss

            b_loss = regularization_loss(bases_stu)
            total_b_loss += b_loss

        pos, neg = pos / batch_size, neg / batch_size
        total_b_loss = total_b_loss / batch_size

        return pos, neg, total_b_loss


def online_assign(feats, bases):
    b, c = feats.size()
    k, _ = bases.size()
    assert bases.size()[1] == c, print(feats.size(), bases.size())

    logits = []
    for i in range(b):
        feat = feats[i].unsqueeze(0)
        simi = F.cosine_similarity(feat, bases, dim=1).unsqueeze(0)
        logits.append(simi)
    logits = torch.concatenate(logits, dim=0)
    logits = F.relu(logits)

    return logits


def regularization_loss(bases):
    k, c = bases.size()
    loss_all = 0
    num = 0
    for i in range(k - 1):
        for j in range(i + 1, k):
            num += 1
            simi = F.cosine_similarity(
                bases[i].unsqueeze(0), bases[j].unsqueeze(0).detach(), dim=1
            )
            simi = F.relu(simi)
            loss_all += simi**2
    loss_all = loss_all / num

    return loss_all


def ce_loss(labels, logits):
    pos_dis = torch.abs(labels - logits)
    pos_loss = -labels * torch.log(1 - pos_dis + 1e-6)
    pos_loss = pos_loss.sum() / (labels.sum() + 1e-6)

    neg_lab = (labels == 0).long()
    neg_loss = neg_lab * (logits**2)
    neg_loss = neg_loss.sum() / (neg_lab.sum() + 1e-6)
    return pos_loss, neg_loss
