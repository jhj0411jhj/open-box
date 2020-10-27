import numpy as np
from typing import List
from sklearn.model_selection import KFold
from litebo.surrogate.tlbo.base import BaseTLSurrogate


_scale_method = 'standardize'


class MFGPE(BaseTLSurrogate):
    def __init__(self, config_space, source_hpo_data, seed,
                 surrogate_type='rf', num_src_hpo_trial=50, only_source=False):
        super().__init__(config_space, source_hpo_data, seed,
                         surrogate_type=surrogate_type, num_src_hpo_trial=num_src_hpo_trial)
        self.method_id = 'mfgpe'
        self.only_source = only_source
        self.build_source_surrogates(normalize='standardize')

        self.scale = True
        if source_hpo_data is not None:
            # Weights for base surrogates and the target surrogate.
            self.w = [1. / self.K] * self.K + [0.]
        self.hist_ws = list()
        self.iteration_id = 0

    def update_trials(self, mf_hpo_data: List):
        self.source_hpo_data = mf_hpo_data
        # Refit the base surrogates.
        self.build_source_surrogates(normalize='standardize')

    def predict_target_surrogate_cv(self, X, y):
        k_fold_num = 5
        _mu, _var = list(), list()

        # Conduct K-fold cross validation.
        kf = KFold(n_splits=k_fold_num)
        idxs = list()
        for train_idx, val_idx in kf.split(X):
            idxs.extend(list(val_idx))
            X_train, X_val, y_train, y_val = X[train_idx,:], X[val_idx,:], y[train_idx], y[val_idx]
            model = self.build_single_surrogate(X_train, y_train, normalize=_scale_method)
            mu, var = model.predict(X_val)
            mu, var = mu.flatten(), var.flatten()
            _mu.extend(list(mu))
            _var.extend(list(var))
        assert (np.array(idxs) == np.arange(X.shape[0])).all()
        return np.asarray(_mu), np.asarray(_var)

    def train(self, X: np.ndarray, y: np.array):
        sample_num = y.shape[0]
        # Build the target surrogate.
        if sample_num >= 3:
            self.target_surrogate = self.build_single_surrogate(X, y, normalize='standardize')
        if self.source_hpo_data is None:
            return

        # Train the target surrogate and update the weight w.
        mu_list, var_list = list(), list()
        for id in range(self.K):
            mu, var = self.source_surrogates[id].predict(X)
            mu_list.append(mu.flatten())
            var_list.append(var.flatten())

        if sample_num >= 5:
            _mu, _var = self.predict_target_surrogate_cv(X, y)
        else:
            _mu, _var = np.zeros(sample_num), np.zeros(sample_num)
        mu_list.append(_mu)
        var_list.append(_var)

        w = self.get_w_ranking_pairs(mu_list, var_list, y)

        self.w = w
        weight_str = ','.join([('%.2f' % item) for item in w])
        self.logger.info('In iter-%d' % self.iteration_id)
        self.target_weight.append(w[-1])
        self.logger.info(weight_str)
        self.hist_ws.append(w)
        self.iteration_id += 1

    def get_w_ranking_pairs(self, mu_list, var_list, y_true):
        preserving_order_p, preserving_order_nums = list(), list()
        for i in range(self.K):
            y_pred = mu_list[i]
            preorder_num, pair_num = self.calculate_preserving_order_num(y_pred, y_true)
            preserving_order_p.append(preorder_num / pair_num)
            preserving_order_nums.append(preorder_num)
        n_power = 3
        trans_order_weight = np.array(preserving_order_p)
        p_power = np.power(trans_order_weight, n_power)
        return p_power / np.sum(p_power)

    def predict(self, X: np.array):
        sample_num = X.shape[0]
        if self.target_surrogate is None:
            mu, var = np.zeros((sample_num, 1)), np.zeros((sample_num, 1))
        else:
            mu, var = self.target_surrogate.predict(X)

        if self.source_hpo_data is None:
            return mu, var

        # Target surrogate predictions with weight.
        mu *= self.w[-1]
        var *= (self.w[-1] * self.w[-1])

        # Base surrogate predictions with corresponding weights.
        for i in range(0, self.K):
            mu_t, var_t = self.source_surrogates[i].predict(X)
            mu += self.w[i] * mu_t
            var += self.w[i] * self.w[i] * var_t
        return mu, var

    def get_weights(self):
        return self.w

    @staticmethod
    def calculate_preserving_order_num(y_pred, y_true):
        array_size = len(y_pred)
        assert len(y_true) == array_size

        total_pair_num, order_preserving_num = 0, 0
        for idx in range(array_size):
            for inner_idx in range(idx + 1, array_size):
                if not ((y_true[idx] > y_true[inner_idx]) ^ (y_pred[idx] > y_pred[inner_idx])):
                    order_preserving_num += 1
                total_pair_num += 1
        return order_preserving_num, total_pair_num
