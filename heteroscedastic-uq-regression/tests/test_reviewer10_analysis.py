from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest
import torch

MODULE_PATH = Path(__file__).resolve().parents[1] / "analysis" / "reviewer10_final_analysis.py"
spec = importlib.util.spec_from_file_location("reviewer10_analysis", MODULE_PATH)
mod = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)


def test_logvariance_clamp_has_zero_gradient_outside_bounds() -> None:
    raw = torch.tensor([[-7.0], [0.0], [7.0]], requires_grad=True)
    clipped = torch.clamp(raw, mod.LOGVAR_MIN, mod.LOGVAR_MAX)
    clipped.sum().backward()
    np.testing.assert_allclose(raw.grad.detach().numpy().ravel(), [0.0, 1.0, 0.0])


def test_exact_mixture_nll_matches_manual_density() -> None:
    y = np.array([0.2, -0.3])
    means = np.array([[0.0, 0.0], [1.0, -1.0]])
    variances = np.array([[1.0, 2.0], [0.5, 1.5]])
    component = np.exp(-0.5 * (np.log(2 * np.pi * variances) + (y[None, :] - means) ** 2 / variances))
    expected = -np.mean(np.log(component.mean(axis=0)))
    assert mod.exact_mixture_nll(y, means, variances) == pytest.approx(expected, rel=1e-12, abs=1e-12)


def test_protocol_b_reuses_one_realized_dataset_within_outer_loop(tmp_path, monkeypatch) -> None:
    calls=[]; data_ids=[]
    def fake_synthetic_dataset(seed:int):
        calls.append(seed); marker=float(seed)
        Xtr=np.full((8,1),marker); ytr=np.arange(8,dtype=float)
        Xte=np.full((4,1),marker); yte=np.arange(4,dtype=float)
        return Xtr,ytr,Xte,yte,np.array(["In-distribution"]*4),yte.copy(),np.ones(4)
    class Data:
        def __init__(self,marker): self.marker=marker
    def fake_prepare(Xtr,ytr,Xte,yte): return Data(float(Xtr[0,0]))
    def fake_fit(data,model,seeds,**kwargs):
        data_ids.append((int(data.marker),id(data)))
        return {"mse":1.0,"nll":2.0,"picp":0.95,"mpiw":3.0,"rece":0.01}
    monkeypatch.setattr(mod,"synthetic_dataset",fake_synthetic_dataset)
    monkeypatch.setattr(mod,"prepare_simple_split",fake_prepare)
    monkeypatch.setattr(mod,"fit_predict_probabilistic",fake_fit)
    monkeypatch.setattr(mod,"RESULTS",tmp_path)
    mod.run_protocol_b(outer_n=3)
    assert calls==[10000,10001,10002]
    for outer_seed in calls:
        ids=[obj_id for marker,obj_id in data_ids if marker==outer_seed]
        assert len(ids)==6 and len(set(ids))==1


def test_finite_sample_conformal_quantile_uses_upper_order_statistic() -> None:
    scores=np.arange(1,20,dtype=float)
    assert mod.conformal_quantile(scores,0.95)==19.0


def test_predictive_draws_have_requested_shape_and_positive_variance() -> None:
    means=np.zeros((3,5)); variances=np.ones((3,5))
    draws=mod.predictive_draws(means,variances,n_draws=25,seed=5)
    assert draws.shape==(25,5)
    assert np.all(np.var(draws,axis=0)>0)
