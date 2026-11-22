"""Hidden Markov Model regimes, CAUSAL.

hmmlearn's ``predict``/``predict_proba`` use the whole sequence (Viterbi / forward-backward
smoothing), so a state at time t would depend on later observations: look-ahead. Here the
model is FITTED on training data only, and states are inferred with the FORWARD FILTER:
P(state_t | observations_1..t). States are relabelled by mean return and volatility
(e.g. "bear", "calm", "bull") so results are comparable across fits.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import numpy.typing as npt
import pandas as pd
from scipy.special import logsumexp
from scipy.stats import multivariate_normal

OBS = ("ret_16", "rvol_96")


@dataclass
class HmmRegime:
    n_states: int = 3
    model: Any = None
    mean: npt.NDArray[np.float64] = field(default_factory=lambda: np.zeros(0))
    scale: npt.NDArray[np.float64] = field(default_factory=lambda: np.ones(0))
    names: list[str] = field(default_factory=list)

    def _obs(self, X: pd.DataFrame) -> npt.NDArray[np.float64]:
        return np.asarray((X[list(OBS)].to_numpy(float) - self.mean) / self.scale)

    def fit(self, X: pd.DataFrame, lengths: list[int] | None = None) -> HmmRegime:
        from hmmlearn.hmm import GaussianHMM

        clean = X[list(OBS)].dropna()
        self.mean = clean.mean().to_numpy(float)
        self.scale = clean.std().replace(0, 1).to_numpy(float)
        self.model = GaussianHMM(n_components=self.n_states, covariance_type="full",
                                 n_iter=100, random_state=7)
        self.model.fit(self._obs(clean), lengths)
        # Name states by mean return (ascending), noting the volatility.
        order = np.argsort(self.model.means_[:, 0])
        self.names = [""] * self.n_states
        labels = ["bear", "neutral", "bull"] if self.n_states == 3 else [
            f"s{i}" for i in range(self.n_states)]
        for rank, k in enumerate(order):
            self.names[k] = labels[rank]
        return self

    def filter(self, X: pd.DataFrame) -> pd.DataFrame:
        """Forward-filtered state probabilities (row t uses rows <= t only). Rows with
        missing observations carry the predicted (prior) distribution forward."""
        m = self.model
        obs = self._obs(X)
        log_a = np.log(m.transmat_)
        log_p = np.log(m.startprob_)
        out = np.full((len(X), self.n_states), np.nan)
        valid = ~np.isnan(obs).any(axis=1)
        ll_all = np.zeros((len(X), self.n_states))
        if valid.any():
            for k in range(self.n_states):       # vectorized emission log-likelihoods
                ll_all[valid, k] = multivariate_normal(m.means_[k], m.covars_[k],
                                                       allow_singular=True).logpdf(obs[valid])
        for t in range(len(X)):
            prior = log_p if t == 0 else logsumexp(log_p[:, None] + log_a, axis=0)
            if valid[t]:
                post = prior + ll_all[t]
                log_p = post - logsumexp(post)
            else:
                log_p = prior
            out[t] = np.exp(log_p)
        cols = [f"hmm_{n}" for n in self.names]
        res = pd.DataFrame(out, index=X.index, columns=cols)
        res["hmm_state"] = [self.names[i] for i in out.argmax(axis=1)]
        return res
