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
    """
    Logistic reg only right now
    """
    def __init__(self, dat: Dataset):
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


class NelderMeadModeler(LockedModeler):
    def __init__(self, dat: Dataset, min_var_idx: int = 1):
        """
        @param min_var_idx: nelder mead only tunes coefficients with idx at least min_var_idx
        """
        self.modeler = LogisticRegression(penalty="none", solver="lbfgs")
        self.dat = dat
        self.modeler.fit(self.dat.x, self.dat.y.flatten())
        self.min_var_idx = min_var_idx
        self.modeler.coef_[0,self.min_var_idx:] = 0

    def do_minimize(self, test_x, test_y, dp_engine, dat_stream=None, maxfev=10):
        """
        @param dat_stream: ignores this
        """
        self.modeler.fit(self.dat.x, self.dat.y.flatten())
        self.modeler.coef_[0,self.min_var_idx:] = 0

        # Just for initialization
        def get_test_perf(params):
            lr = sklearn.base.clone(self.modeler)
            #print(params)
            lr = self.set_model(lr, np.concatenate([
                self.modeler.intercept_,
                self.modeler.coef_.flatten()[:self.min_var_idx],
                params]))
            pred_y = lr.predict_proba(test_x)[:,1].reshape((-1,1))
            mtp_answer = dp_engine.get_test_eval(test_y, pred_y)
            return mtp_answer

        test_hist = TestHistory(self.modeler)
        init_coef = np.concatenate([self.modeler.coef_.flatten()[self.min_var_idx:]])
        # TODO: add callback to append to history
        res = scipy.optimize.minimize(get_test_perf, x0=init_coef, method="Nelder-Mead", options={"maxfev": maxfev, "adaptive": True})
        print(res.x)
        self.modeler = self.set_model(self.modeler, np.concatenate([
                self.modeler.intercept_,
                self.modeler.coef_.flatten()[:self.min_var_idx],
                res.x]))

        return test_hist

class CtsAdversaryModeler(LockedModeler):
    def __init__(self, dat: Dataset, min_var_idx: int = 1, update_incr: float = 0.04):
        """
        @param min_var_idx: nelder mead only tunes coefficients with idx at least min_var_idx
        """
        self.modeler = LogisticRegression(penalty="none", solver="lbfgs")
        self.dat = dat
        self.update_incr = update_incr
        self.min_var_idx = min_var_idx
        self.modeler.fit(self.dat.x, self.dat.y.flatten())
        self.modeler.coef_[0,:self.min_var_idx] = 5
        self.modeler.coef_[0,self.min_var_idx:] = 0

    def do_minimize(self, test_x, test_y, dp_engine, dat_stream=None, maxfev=10):
        """
        @param dat_stream: ignores this
        """
        # Train a good initial model
        self.modeler.fit(self.dat.x, self.dat.y.flatten())
        self.modeler.intercept_[:] = 0
        self.modeler.coef_[0,:self.min_var_idx] = 5
        self.modeler.coef_[0,self.min_var_idx:] = 0

        def get_test_perf(params):
            lr = sklearn.base.clone(self.modeler)
            lr = self.set_model(lr, params)
            pred_y = lr.predict_proba(test_x)[:,1].reshape((-1,1))
            mtp_answer = dp_engine.get_test_eval(test_y, pred_y)
            return mtp_answer

        # Now search in each direction and do a greedy search
        test_hist = TestHistory(self.modeler)
        curr_coef = np.concatenate([self.modeler.intercept_, self.modeler.coef_.flatten()])
        curr_perf = get_test_perf(curr_coef)
        while test_hist.curr_time < maxfev:
            # Test each variable (that's known to be irrelevant)
            for var_idx in range(self.min_var_idx, test_x.shape[1]):
                # Test each direction for the variable
                is_success = False
                for update_dir in [-1,1]:
                    if test_hist.curr_time >= maxfev:
                        break
                    curr_coef = np.concatenate([self.modeler.intercept_, self.modeler.coef_.flatten()])
                    curr_coef[var_idx] += update_dir * self.update_incr
                    test_res = get_test_perf(curr_coef)
                    test_hist.update(
                            test_res=test_res,
                            curr_mdl=self.modeler)
                    #print(test_res, curr_perf, var_idx, update_dir)
                    if test_res < curr_perf:
                        is_success = True
                        self.set_model(self.modeler, curr_coef)
                        curr_perf = test_res
                        break

                # If we found a good direction, keep walking in that direction
                while is_success:
                    if test_hist.curr_time >= maxfev:
                        break
                    curr_coef = np.concatenate([self.modeler.intercept_, self.modeler.coef_.flatten()])
                    curr_coef[var_idx] += update_dir * self.update_incr
                    test_res = get_test_perf(curr_coef)
                    test_hist.update(
                            test_res=test_res,
                            curr_mdl=self.modeler)
                    if test_res < curr_perf:
                        self.set_model(self.modeler, curr_coef)
                        curr_perf = test_res
                        is_success = True
                    else:
                        is_success = False

        return test_hist

class BinaryAdversaryModeler(LockedModeler):
    def __init__(self, dat: Dataset, min_var_idx: int = 1, update_incr: float = 0.04):
        """
        @param min_var_idx: nelder mead only tunes coefficients with idx at least min_var_idx
        """
        self.modeler = LogisticRegression(penalty="none", solver="lbfgs")
        self.dat = dat
        self.update_incr = update_incr
        self.min_var_idx = min_var_idx
        self.modeler.fit(self.dat.x, self.dat.y.flatten())
        self.modeler.coef_[0,:self.min_var_idx] = 5
        self.modeler.coef_[0,self.min_var_idx:] = 0

    def do_minimize(self, test_x, test_y, dp_engine, dat_stream=None, maxfev=10):
        """
        @param dat_stream: ignores this
        """
        # Train a good initial model
        self.modeler.fit(self.dat.x, self.dat.y.flatten())
        self.modeler.coef_[0,:self.min_var_idx] = 5
        self.modeler.coef_[0,self.min_var_idx:] = 0
        orig_pred_y = self.modeler.predict_proba(test_x)[:,1].reshape((-1,1))


        def get_test_perf(params):
            lr = sklearn.base.clone(self.modeler)
            lr = self.set_model(lr, params)
            pred_y = lr.predict_proba(test_x)[:,1].reshape((-1,1))
            prev_pred_y = self.modeler.predict_proba(test_x)[:,1].reshape((-1,1))
            mtp_answer = dp_engine.get_test_compare(test_y, pred_y, prev_pred_y, predef_pred_y=orig_pred_y)
            return mtp_answer

        # Now search in each direction and do a greedy search
        test_hist = TestHistory(self.modeler)
        while test_hist.curr_time < maxfev:
            # Test each variable (that's known to be irrelevant)
            for var_idx in range(self.min_var_idx, test_x.shape[1]):
                # Test each direction for the variable
                for update_dir in [-1,1]:
                    if test_hist.curr_time >= maxfev:
                        break
                    curr_coef = np.concatenate([self.modeler.intercept_, self.modeler.coef_.flatten()])
                    curr_coef[var_idx] += update_dir * self.update_incr
                    test_res = get_test_perf(curr_coef)
                    test_hist.update(
                            test_res=test_res,
                            curr_mdl=self.modeler)
                    if test_res == 1:
                        self.set_model(self.modeler, curr_coef)
                        break

                # If we found a good direction, keep walking in that direction
                while test_res == 1:
                    if test_hist.curr_time >= maxfev:
                        break
                    curr_coef = np.concatenate([self.modeler.intercept_, self.modeler.coef_.flatten()])
                    curr_coef[var_idx] += update_dir * self.update_incr
                    test_res = get_test_perf(curr_coef)
                    test_hist.update(
                            test_res=test_res,
                            curr_mdl=self.modeler)
                    if test_res == 1:
                        self.set_model(self.modeler, curr_coef)
        print("coefs", self.modeler.coef_)
        return test_hist

class AdversarialModeler(LockedModeler):
    def __init__(self, dat, min_var_idx: int = 1):
        self.cts_modeler = CtsAdversaryModeler(dat, min_var_idx)
        self.binary_modeler = BinaryAdversaryModeler(dat, min_var_idx)
        self.modeler = self.cts_modeler.modeler

    def do_minimize(self, test_x, test_y, dp_engine, dat_stream=None, maxfev=10):
        """
        @param dat_stream: ignores this

        @return perf_value
        """
        if dp_engine.name == "no_dp":
            test_hist = self.cts_modeler.do_minimize(test_x, test_y, dp_engine, dat_stream, maxfev)
            self.modeler = self.cts_modeler.modeler
        else:
            test_hist = self.binary_modeler.do_minimize(test_x, test_y, dp_engine, dat_stream, maxfev)
            self.modeler = self.binary_modeler.modeler
        return test_hist

class OnlineLearnerModeler(LockedModeler):
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
