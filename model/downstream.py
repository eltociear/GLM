# coding=utf-8
# Copyright (c) 2020, NVIDIA CORPORATION.  All rights reserved.
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

"""Multiple choice model."""

import torch
import torch.nn
from .gpt2_modeling import GPT2Model


class ClozeModel(torch.nn.Module):
    def __init__(self, language_model, take_softmax=True, length_penalty=0.0):
        super(ClozeModel, self).__init__()
        self.model = language_model
        self.take_softmax = take_softmax
        self.length_penalty = length_penalty

    def forward(self, input_ids, position_ids, attention_mask, target_ids, logit_mask):
        num_choices = None
        if len(input_ids.shape) == 3:
            batch_size, num_choices = input_ids.shape[:2]
            input_ids = input_ids.reshape(-1, input_ids.size(-1))
            attention_mask = attention_mask.reshape(-1, *attention_mask.size()[2:])
            position_ids = position_ids.reshape(-1, *position_ids.size()[2:])
            target_ids = target_ids.reshape(-1, target_ids.size(-1))
            logit_mask = logit_mask.reshape(-1, logit_mask.size(-1))
        outputs, *mems = self.model(input_ids, position_ids, attention_mask)
        if self.take_softmax:
            outputs = torch.nn.functional.log_softmax(outputs, dim=-1)
        batch_ids = torch.arange(target_ids.size(0), dtype=torch.long, device=target_ids.device)
        batch_ids = batch_ids.unsqueeze(1).expand_as(target_ids)
        seq_ids = torch.arange(target_ids.size(-1), dtype=torch.long, device=target_ids.device)
        seq_ids = seq_ids.unsqueeze(0).expand_as(target_ids)
        logits = outputs[batch_ids, seq_ids, target_ids]
        logits = (logits * logit_mask).sum(dim=1)
        if self.length_penalty > 0.0:
            logits = logits / logit_mask.sum(dim=1) ** self.length_penalty
        if num_choices is not None:
            logits = logits.view(-1, num_choices)
        return (logits, *mems)


class VerbalizerModel(torch.nn.Module):
    def __init__(self, language_model):
        super().__init__()
        self.model = language_model

    def forward(self, input_ids, position_ids, attention_mask, target_ids, logit_mask):
        assert len(input_ids.shape) == 2
        outputs, *mems = self.model(input_ids, position_ids, attention_mask)
        batch_ids = torch.arange(outputs.size(0), dtype=attention_mask.dtype, device=attention_mask.device)
        output = outputs[batch_ids, attention_mask]
        batch_ids = batch_ids.unsqueeze(1).expand_as(target_ids)
        output = output[batch_ids, target_ids]
        return (output, *mems)


class PoolingModel(torch.nn.Module):
    def __init__(self, language_model, hidden_size, hidden_dropout, pool_token, num_class=1):
        super().__init__()
        self.pool_token = pool_token
        self.model = language_model
        self.num_class = num_class
        # Multi-choice head.
        self.pool_layer = torch.nn.Linear(hidden_size, hidden_size)
        self.multichoice_dropout = torch.nn.Dropout(hidden_dropout)
        self.multichoice_head = torch.nn.Linear(hidden_size, num_class)

    def forward(self, input_ids, position_ids, attention_mask):
        num_choices = None
        if len(input_ids.shape) == 3:
            assert self.num_class == 1
            batch_size, num_choices = input_ids.shape[:2]
            input_ids = input_ids.reshape(-1, input_ids.size(-1))
            attention_mask = attention_mask.reshape(-1, *attention_mask.size()[2:])
            position_ids = position_ids.reshape(-1, *position_ids.size()[2:])
        outputs, *mems = self.model(input_ids, position_ids, attention_mask)
        if self.pool_token == 'start':
            output = outputs[
                torch.arange(outputs.size(0), dtype=attention_mask.dtype, device=attention_mask.device), attention_mask]
        elif self.pool_token == 'pad':
            output = outputs[torch.arange(outputs.size(0), dtype=attention_mask.dtype,
                                          device=attention_mask.device), attention_mask - 1]
        elif self.pool_token == 'cls':
            output = outputs[:, 0]
        else:
            raise NotImplementedError
        output = torch.tanh(self.pool_layer(output))
        multichoice_output = self.multichoice_dropout(output)
        logits = self.multichoice_head(multichoice_output)
        if num_choices is not None:
            logits = logits.view(-1, num_choices)
        return (logits, *mems)
