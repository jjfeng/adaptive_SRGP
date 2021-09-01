from typing import List
import numpy as np
import pandas as pd
import scipy.optimize
import sklearn.base
from sklearn.linear_model import LinearRegression, LogisticRegression
from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier

from dataset import Dataset

class TestHistory:
    def __init__(self, init_model):
        self.approval_times = [0]
        self.approved_mdls = [init_model]
        self.curr_time = 1

    def update(self, test_res, curr_mdl):
        if test_res == 1:
            self.approval_times.append(self.curr_time)
            self.approved_mdls.append(curr_mdl)

        self.curr_time += 1

class LockedModeler:
    def __init__(self, dat: Dataset, n_estimators: int=200, max_depth: int = 3):
        self.dat = dat
        self.curr_model = GradientBoostingClassifier(n_estimators=n_estimators, learning_rate=.04, max_depth=max_depth, random_state=0)
        self._fit_model()
        self.refit_freq = None

    def _fit_model(self):
        self.curr_model.fit(self.dat.x, self.dat.y.flatten())

    def predict_prob_single(self, x):
        return self.curr_model.predict_proba(x)[:,1].reshape((-1,1))

    def predict_prob(self, x):
        return self.curr_model.predict_proba(x)[:,1].reshape((-1,1))

    def update(self, x, y, is_init=False):
        """
        @return whether or not the underlying model changed
        """
        # Do nothing
        return False

class NelderMeadModeler:
    """
    Logistic reg only right now
    """
    def __init__(self, dat):
        self.modeler = LogisticRegression(penalty="none", solver="lbfgs")
        self.dat = dat
        self.modeler.fit(self.dat.x, self.dat.y.flatten())

    def set_model(self, mdl, params):
        mdl.classes_ = np.array([0,1])
        mdl.coef_ = params[1:].reshape((1,-1))
        mdl.intercept_ = np.array([params[0]])
        return mdl

    def predict_prob(self, x):
        return self.modeler.predict_proba(x)[:,1].reshape((-1,1))

    def do_minimize(self, test_x, test_y, dp_engine, dat_stream=None, maxfev=10):
        """
        @param dat_stream: ignores this

        @return perf_value
        """
        self.modeler.fit(self.dat.x, self.dat.y.flatten())

        # Just for initialization
        def get_test_perf(params):
            lr = sklearn.base.clone(self.modeler)
            #lr.fit(test_x[:5], test_y[:5])
            #print(lr.coef_.shape, lr.intercept_, test_x.shape)
            lr = self.set_model(lr, params)
            pred_y = lr.predict_proba(test_x)[:,1].reshape((-1,1))
            mtp_answer = dp_engine.get_test_eval(test_y, pred_y)
            return mtp_answer

        test_hist = TestHistory(self.modeler)
        init_coef = np.concatenate([self.modeler.intercept_, self.modeler.coef_.flatten()])
        # TODO: add callback to append to history
        res = scipy.optimize.minimize(get_test_perf, x0=init_coef, method="Nelder-Mead", options={"maxfev": maxfev})
        self.modeler = self.set_model(self.modeler, res.x)

        return test_hist

class OnlineLearnerModeler(NelderMeadModeler):
    """
    Just do online learning on a separate dataset
    only does logistic reg
    """
    def do_minimize(self, test_x, test_y, dp_engine, dat_stream, maxfev=10):
        """
        @param dat_stream: a list of datasets for further training the model
        @return perf_value
        """
        self.modeler.fit(self.dat.x, self.dat.y.flatten())

        merged_dat = self.dat
        test_hist = TestHistory(self.modeler)
        for i, batch_dat in enumerate(dat_stream[:maxfev]):
            merged_dat = Dataset.merge([merged_dat, batch_dat])
            lr = sklearn.base.clone(self.modeler)
            lr.fit(merged_dat.x, merged_dat.y.flatten())

            pred_y = lr.predict_proba(test_x)[:,1].reshape((-1,1))
            test_res = dp_engine.get_test_eval(test_y, pred_y)
            if test_res == 1:
                # replace current modeler only if successful
                self.modeler = lr
            test_hist.update(
                    test_res=test_res,
                    curr_mdl=self.modeler)

        return test_hist

class OnlineLearnerFixedModeler(OnlineLearnerModeler):
    """
    Just do online learning on a separate dataset
    only does logistic reg
    """
    def do_minimize(self, test_x, test_y, dp_engine, dat_stream, maxfev=10):
        """
        @param dat_stream: a list of datasets for further training the model
        @return perf_value
        """
        self.modeler.fit(self.dat.x, self.dat.y.flatten())

        merged_dat = self.dat
        test_hist = TestHistory(self.modeler)
        for i, batch_dat in enumerate(dat_stream[:maxfev]):
            merged_dat = Dataset.merge([merged_dat, batch_dat])
            lr = sklearn.base.clone(self.modeler)
            lr.fit(merged_dat.x, merged_dat.y.flatten())

            pred_y = lr.predict_proba(test_x)[:,1].reshape((-1,1))
            test_res = dp_engine.get_test_eval(test_y, pred_y, predef_pred_y=pred_y)
            if test_res == 1:
                # replace current modeler only if successful
                self.modeler = lr
            test_hist.update(
                    test_res=test_res,
                    curr_mdl=self.modeler)
        return test_hist
