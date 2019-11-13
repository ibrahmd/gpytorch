#!/usr/bin/env python3

import math

import torch
from pyro import distributions as dist
from pyro.distributions.transforms import BlockAutoregressive

from .kernel import Kernel


class SpectralAutoregressiveFlowKernel(Kernel):
    def __init__(self, num_dims, stack_size=1, **kwargs):
        super(SpectralAutoregressiveFlowKernel, self).__init__(has_lengthscale=True, **kwargs)
        if stack_size > 1:
            self.dsf = torch.nn.ModuleList([BlockAutoregressive(num_dims, **kwargs) for _ in range(stack_size)])
        else:
            self.dsf = BlockAutoregressive(num_dims, **kwargs)
        self.num_dims = num_dims

    def _create_input_grid(self, x1, x2, diag=False, last_dim_is_batch=False, **params):
        """
        This is a helper method for creating a grid of the kernel's inputs.
        Use this helper rather than maually creating a meshgrid.

        The grid dimensions depend on the kernel's evaluation mode.

        Args:
            :attr:`x1` (Tensor `n x d` or `b x n x d`)
            :attr:`x2` (Tensor `m x d` or `b x m x d`) - for diag mode, these must be the same inputs

        Returns:
            (:class:`Tensor`, :class:`Tensor) corresponding to the gridded `x1` and `x2`.
            The shape depends on the kernel's mode

            * `full_covar`: (`b x n x 1 x d` and `b x 1 x m x d`)
            * `full_covar` with `last_dim_is_batch=True`: (`b x k x n x 1 x 1` and `b x k x 1 x m x 1`)
            * `diag`: (`b x n x d` and `b x n x d`)
            * `diag` with `last_dim_is_batch=True`: (`b x k x n x 1` and `b x k x n x 1`)
        """
        x1_, x2_ = x1, x2
        if last_dim_is_batch:
            x1_ = x1_.transpose(-1, -2).unsqueeze(-1)
            if torch.equal(x1, x2):
                x2_ = x1_
            else:
                x2_ = x2_.transpose(-1, -2).unsqueeze(-1)

        if diag:
            return x1_, x2_
        else:
            return x1_.unsqueeze(-2), x2_.unsqueeze(-3)

    def forward(self, x1, x2, diag=False, **params):
        x1_ = x1.div(self.lengthscale)
        x2_ = x2.div(self.lengthscale)
        x1_, x2_ = self._create_input_grid(x1_, x2_, diag=diag)

        diffs = x1_ - x2_
        base_dist = dist.Normal(
            torch.zeros(self.num_dims, device=x1.device, dtype=x1.dtype),
            torch.ones(self.num_dims, device=x1.device, dtype=x1.dtype),
        )
        if isinstance(self.dsf, torch.nn.ModuleList):
            dsf = list(self.dsf)
        else:
            dsf = self.dsf
        dsf_dist = dist.TransformedDistribution(base_dist, dsf)
        if self.training:
            Z = dsf_dist.rsample(torch.Size([2000]))
        else:
            Z = dsf_dist.rsample(torch.Size([2000]))

        if diag:
            diffs_times_Z = (Z.unsqueeze(-2) * diffs.unsqueeze(-3)).sum(-1)
            K = diffs_times_Z.mul(2 * math.pi).cos().mean(dim=-2)
        else:
            diffs_times_Z = (Z.unsqueeze(-2).unsqueeze(-2) * diffs.unsqueeze(-4)).sum(-1)
            K = diffs_times_Z.mul(2 * math.pi).cos().mean(dim=-3)
        return K


class TrapezoidalSpectralAutoregressiveFlowKernel(SpectralAutoregressiveFlowKernel):
    def __init__(self, num_dims, hidden_factors=[8, 8], **kwargs):
        super().__init__(num_dims, hidden_factors, **kwargs)

    def forward(self, x1, x2, diag=False, **params):
        """
        TODO: Update to not be a product kernel in D>1
        """
        x1_ = x1.div(self.lengthscale)
        x2_ = x2.div(self.lengthscale)
        x1_, x2_ = self._create_input_grid(x1_, x2_, diag=diag)

        diffs = x1_ - x2_
        base_dist = dist.Normal(
            torch.zeros(self.num_dims, device=x1.device, dtype=x1.dtype),
            torch.ones(self.num_dims, device=x1.device, dtype=x1.dtype),
        )
        if isinstance(self.dsf, torch.nn.ModuleList):
            dsf = list(self.dsf)
        else:
            dsf = self.dsf
        dsf_dist = dist.TransformedDistribution(base_dist, dsf)

        if diag:
            samples = dsf_dist.rsample(torch.Size([1000]))
            diffs_times_Z = (samples.unsqueeze(-2) * diffs.unsqueeze(-3)).sum(-1)
            log_probs = dsf_dist.log_prob(samples)
            norm_log_probs = log_probs - torch.logsumexp(log_probs, -1)
            norm_probs = norm_log_probs.exp().view(1000, 1)

            values = diffs_times_Z.mul(2 * math.pi).cos().mul(norm_probs)  # I x n x n x d
            K = values.mean(-2)
        else:
            samples = dsf_dist.rsample(torch.Size([1000]))
            diffs_times_Z = (samples.unsqueeze(-2).unsqueeze(-2) * diffs.unsqueeze(-4)).sum(-1)
            log_probs = dsf_dist.log_prob(samples)
            norm_log_probs = log_probs - torch.logsumexp(log_probs, -1)
            norm_probs = norm_log_probs.exp().view(1000, 1, 1)

            values = diffs_times_Z.mul_(2 * math.pi).cos_().mul_(norm_probs)  # I x n x n x d
            K = values.mean(-3)

        return K
